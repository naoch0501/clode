"""当日のレースを発走時刻順に監視し、締切前に判定→上限チェック→投票を行う。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from .config import Config
from .datasource.base import DataSource
from .ledger import Ledger
from .models import JST, Bet, Race
from .notify import Notifier
from .risk import RiskManager
from .strategy.rules import Decision, StrategySet
from .voter.base import HaltError, Voter

log = logging.getLogger(__name__)


@dataclass
class RaceOutcome:
    race: Race
    decisions: list[Decision] = field(default_factory=list)
    placed: list[Bet] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (戦略, 理由)
    message: str = ""


class Engine:
    def __init__(
        self,
        cfg: Config,
        source: DataSource,
        strategies: StrategySet,
        voter: Voter,
        ledger: Ledger,
        notifier: Notifier | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(JST),
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cfg = cfg
        self.source = source
        self.strategies = strategies
        self.voter = voter
        self.ledger = ledger
        self.notifier = notifier or Notifier()
        self.clock = clock
        self.sleep = sleep
        self.risk = RiskManager(cfg.limits, ledger, voter.mode, cfg.schedule.cutoff_minutes)

    # -- 1 レース

    def process_race(self, race: Race) -> RaceOutcome:
        card = self.source.get_card(race.race_id)
        # 一覧ページの情報で補完（オッズページ側が優先）
        card.race.track = card.race.track or race.track
        card.race.post_time = card.race.post_time or race.post_time
        out = RaceOutcome(card.race)
        out.decisions = self.strategies.evaluate(card)
        matched = [d for d in out.decisions if d.matched]
        for d in out.decisions:
            log.info("%s %dR [%s] %s: %s", card.race.track, card.race.race_no, d.strategy,
                     "合致" if d.matched else "見送り", d.reason)
        if not matched:
            return out

        balance = self.voter.balance()
        approved: list[Bet] = []
        for d in matched:
            res = self.risk.check(
                card.race,
                d.strategy,
                d.bets,
                balance=balance,
                now=self.clock(),
                pending_race=sum(b.amount for b in approved),
                pending_points=len(approved),
            )
            if res.ok:
                approved.extend(d.bets)
            else:
                log.warning("[%s] 上限チェックで投票しません: %s", d.strategy, res.reason)
                out.rejected.append((d.strategy, res.reason))
                for b in d.bets:
                    self.ledger.record(b, self.voter.mode, "rejected", res.reason)
        if not approved:
            return out

        try:
            result = self.voter.place(approved)
        except HaltError as ex:
            for b in approved:
                self.ledger.record(b, self.voter.mode, "failed", f"停止: {ex}")
            self.notifier.send(f"⚠ 自動投票を停止しました: {card.race.track}{card.race.race_no}R {ex}")
            raise
        status = "placed" if result.ok else "failed"
        for b in approved:
            self.ledger.record(b, self.voter.mode, status, result.message)
        out.message = result.message
        if result.ok:
            out.placed = approved
        mode = "模擬" if self.voter.mode == "dry" else "実"
        lines = "\n".join(f"・{b.bet_type} {b.selection_str} {b.amount}円 [{b.strategy}]" for b in approved)
        self.notifier.send(
            f"{'✅' if result.ok else '❌'} {mode}投票 {card.race.track}{card.race.race_no}R"
            f" {card.race.post_time:%H:%M}発走\n{lines}\n{result.message}"
        )
        return out

    # -- 1 日

    def _target_races(self, day: date) -> list[Race]:
        races = self.source.list_races(day)
        if self.cfg.tracks:
            want = {t.replace(" ", "").replace("　", "") for t in self.cfg.tracks}
            races = [r for r in races if r.track.replace(" ", "").replace("　", "") in want]
        return [r for r in races if r.post_time and "中止" not in (r.status + r.name)]

    def run_day(self, day: date, once: bool = False) -> list[RaceOutcome]:
        """once=True: いま投票可能な全レースを即時に 1 回ずつ判定して終了（試運転用）。"""
        sch = self.cfg.schedule
        lead = timedelta(minutes=sch.lead_minutes)
        cutoff = timedelta(minutes=sch.cutoff_minutes)
        pending = self._target_races(day)
        log.info("対象 %d レース（%s）", len(pending), ", ".join(sorted({r.track for r in pending})) or "なし")
        outcomes: list[RaceOutcome] = []
        while pending:
            now = self.clock()
            still: list[Race] = []
            for r in pending:
                assert r.post_time
                if now > r.post_time - cutoff:
                    log.debug("締切済みのため対象外: %s %dR", r.track, r.race_no)
                    continue
                if once or now >= r.post_time - lead:
                    try:
                        outcomes.append(self.process_race(r))
                    except HaltError:
                        raise
                    except Exception as ex:  # 1 レースの取得失敗などで全体を止めない
                        log.exception("%s %dR の処理に失敗: %s", r.track, r.race_no, ex)
                else:
                    still.append(r)
            if once:
                break
            pending = still
            if pending:
                nxt = min(r.post_time - lead for r in pending)  # type: ignore[operator]
                wait = max(1.0, min(sch.poll_seconds, (nxt - self.clock()).total_seconds()))
                self.sleep(wait)
        log.info("本日の対象レースは終了しました")
        return outcomes
