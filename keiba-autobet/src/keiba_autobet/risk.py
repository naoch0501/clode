"""投票前の上限チェック。1 戦略の買い目はまとめて可否を決める（一部だけ買うことはしない）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Limits
from .ledger import Ledger
from .models import JST, Bet, Race


@dataclass
class RiskResult:
    ok: bool
    reason: str = ""


class RiskManager:
    def __init__(self, limits: Limits, ledger: Ledger, mode: str, cutoff_minutes: float):
        self.limits = limits
        self.ledger = ledger
        self.mode = mode
        self.cutoff = timedelta(minutes=cutoff_minutes)

    def check(
        self,
        race: Race,
        strategy: str,
        bets: list[Bet],
        balance: int | None = None,
        now: datetime | None = None,
        pending_race: int = 0,
        pending_points: int = 0,
    ) -> RiskResult:
        now = now or datetime.now(JST)
        lim = self.limits
        if not bets:
            return RiskResult(False, "買い目なし")
        if race.post_time is None:
            return RiskResult(False, "発走時刻が不明")
        if now > race.post_time - self.cutoff:
            return RiskResult(False, f"締切間際（発走 {race.post_time:%H:%M} の {self.cutoff} 前を過ぎた）")
        if self.ledger.already_bet(race.race_id, strategy, self.mode):
            return RiskResult(False, "このレース・戦略は投票済み")
        for b in bets:
            if b.amount > lim.max_per_bet:
                return RiskResult(False, f"1 点 {b.amount} 円が max_per_bet={lim.max_per_bet} を超過")
            if b.amount % 100:
                return RiskResult(False, f"金額が 100 円単位ではない: {b.amount}")
        total = sum(b.amount for b in bets)
        if pending_points + len(bets) > lim.max_points_per_race:
            return RiskResult(False, f"点数が max_points_per_race={lim.max_points_per_race} を超過")
        race_total = self.ledger.spent_on_race(race.race_id, self.mode) + pending_race + total
        if race_total > lim.max_per_race:
            return RiskResult(False, f"レース合計 {race_total} 円が max_per_race={lim.max_per_race} を超過")
        day_total = self.ledger.spent_on_day(race.date, self.mode) + pending_race + total
        if day_total > lim.max_per_day:
            return RiskResult(False, f"本日合計 {day_total} 円が max_per_day={lim.max_per_day} を超過")
        if balance is not None and balance - pending_race - total < lim.min_balance:
            return RiskResult(False, f"残高 {balance} 円から購入すると min_balance={lim.min_balance} を下回る")
        return RiskResult(True)
