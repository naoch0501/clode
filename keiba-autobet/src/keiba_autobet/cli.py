"""コマンドライン: keiba-autobet <command>"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import Config, load_config
from .datasource.rakuten import RakutenDataSource
from .ledger import Ledger
from .models import JST
from .notify import Notifier
from .scheduler import Engine
from .strategy.rules import load_strategies
from .voter.base import HaltError
from .voter.dryrun import DryRunVoter

log = logging.getLogger("keiba_autobet")


def _day(s: str) -> date:
    today = datetime.now(JST).date()
    if s in ("today", "今日"):
        return today
    if s in ("tomorrow", "明日"):
        return today + timedelta(days=1)
    return datetime.strptime(s.replace("/", "-"), "%Y-%m-%d").date()


def _setup_logging(cfg: Config, verbose: bool) -> None:
    for s in (sys.stdout, sys.stderr):  # Windows のコンソールで日本語が化けないように
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    log_dir = cfg.resolve(cfg.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    fh = logging.FileHandler(log_dir / f"{datetime.now(JST):%Y%m%d}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.handlers[:] = [sh, fh]
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _make_voter(cfg: Config, live: bool, submit: bool = True):
    if not live:
        return DryRunVoter(cfg.voter.get("dry_balance"))
    if not cfg.live_enabled:
        raise SystemExit("--live が指定されましたが config.yaml の live_enabled が true ではありません。実投票しません。")
    from .voter.rakuten import RakutenVoter

    v = cfg.voter
    return RakutenVoter(
        selectors_path=cfg.resolve(v["selectors"]) if v.get("selectors") else None,
        profile_dir=cfg.resolve(v.get("profile_dir", "data/browser-profile")),
        shot_dir=cfg.resolve(cfg.log_dir) / "screenshots",
        headless=bool(v.get("headless", False)),
        submit=submit,
        slow_mo_ms=int(v.get("slow_mo_ms", 100)),
        channel=v.get("channel"),
        executable_path=v.get("executable_path"),
    )


# ---------------------------------------------------------------- コマンド


def cmd_races(args, cfg: Config) -> None:
    ds = RakutenDataSource()
    for r in ds.list_races(_day(args.date)):
        t = f"{r.post_time:%H:%M}" if r.post_time else "--:--"
        print(f"{r.race_id}  {t}  {r.track:<4} {r.race_no:>2}R  {r.surface}{r.distance or '':<5} "
              f"{r.runners or '':>2}頭  {r.status:<4} {r.name}")


def cmd_show(args, cfg: Config) -> None:
    card = RakutenDataSource().get_card(args.race_id)
    r = card.race
    post = f"{r.post_time:%H:%M}" if r.post_time else "--:--"
    print(f"{r.track} {r.race_no}R {r.name}  {r.surface}{r.distance}m  {r.weather}/{r.going}  発走 {post}")
    print("枠 馬番 馬名               騎手     調教師   単勝   複勝        人気")
    for e in card.entries:
        if e.scratched:
            print(f"{e.frame:>2} {e.number:>3}  {e.horse:<16} 取消")
            continue
        print(f"{e.frame:>2} {e.number:>3}  {e.horse:<16} {e.jockey:<6} {e.trainer:<6} "
              f"{e.win_odds or '-':>6} {e.place_odds_min or '-':>5}-{e.place_odds_max or '-':<5} {e.popularity or '-':>3}")


def cmd_eval(args, cfg: Config) -> None:
    ss = load_strategies(args.strategies)
    card = RakutenDataSource().get_card(args.race_id)
    print(f"{card.race.track} {card.race.race_no}R {card.race.name}")
    for d in ss.evaluate(card):
        print(f"[{'合致' if d.matched else '見送り'}] {d.strategy}: {d.reason}")
        for b in d.bets:
            print(f"    {b.bet_type} {b.selection_str} {b.amount}円")


def cmd_run(args, cfg: Config) -> None:
    ss = load_strategies(args.strategies)
    voter = _make_voter(cfg, args.live)
    if args.live:
        lim = cfg.limits
        log.warning("★ 実投票モード ★ 上限: 1点 %d円 / 1レース %d円 / 1日 %d円 / 残高下限 %d円",
                    lim.max_per_bet, lim.max_per_race, lim.max_per_day, lim.min_balance)
    ledger = Ledger(cfg.resolve(cfg.ledger_path))
    notifier = Notifier(cfg.webhook_url)
    engine = Engine(cfg, RakutenDataSource(), ss, voter, ledger, notifier)
    voter.start()
    try:
        outcomes = engine.run_day(_day(args.date), once=args.once)
    except HaltError as ex:
        log.error("安全のため停止しました: %s", ex)
        raise SystemExit(2)
    finally:
        voter.close()
    placed = [b for o in outcomes for b in o.placed]
    summary = f"終了: 投票 {len(placed)} 点 / {sum(b.amount for b in placed)} 円（{voter.mode}）"
    log.info(summary)
    notifier.send(summary)


def cmd_check(args, cfg: Config) -> None:
    """楽天競馬にログインし、残高を読み、（--race 指定時）確認画面まで進めて止まる。投票はしない。"""
    if not cfg.live_enabled:
        log.info("live_enabled=false ですが check は確定ボタンを押さないので実行します")
    cfg.live_enabled = True
    cfg.voter["headless"] = not args.headed
    voter = _make_voter(cfg, live=True, submit=False)
    voter.start()
    try:
        print("ログイン OK")
        print("残高:", voter.balance())
        if args.race:
            from .models import Bet

            bet = Bet(args.race, "check", "単勝", (int(args.number),), 100)
            print("確認画面まで進めます（確定はしません）:", bet)
            print(voter.place([bet]).message)
        if args.headed:
            print("Playwright Inspector で画面を調べられます。終わったら Inspector の再生ボタン（Resume）を押してください。")
            voter.page.pause()
    finally:
        voter.close()


def cmd_history(args, cfg: Config) -> None:
    ledger = Ledger(cfg.resolve(cfg.ledger_path))
    rows = ledger.history(_day(args.date) if args.date else None)
    for r in reversed(rows):
        print(f"{r['created_at']}  {r['mode']:<4} {r['status']:<8} {r['race_id']} {r['bet_type']} "
              f"{r['selection']:<9} {r['amount']:>6}円 [{r['strategy']}] {r['reason']}")


def cmd_set_credentials(args, cfg: Config) -> None:
    from . import credentials

    credentials.save_interactive()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="keiba-autobet", description="地方競馬（楽天競馬）条件付き自動投票")
    p.add_argument("-c", "--config", default="config.yaml", help="設定ファイル（既定: config.yaml）")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("races", help="開催レース一覧")
    s.add_argument("--date", default="today")
    s.set_defaults(func=cmd_races)

    s = sub.add_parser("show", help="出走馬とオッズを表示")
    s.add_argument("race_id")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("eval", help="1 レースだけ条件を判定（投票しない）")
    s.add_argument("race_id")
    s.add_argument("-s", "--strategies", default="strategies/example.yaml")
    s.set_defaults(func=cmd_eval)

    s = sub.add_parser("run", help="当日のレースを監視して自動投票（既定は模擬投票）")
    s.add_argument("--date", default="today")
    s.add_argument("-s", "--strategies", default="strategies/example.yaml")
    s.add_argument("--live", action="store_true", help="実際に投票する（config の live_enabled: true も必要）")
    s.add_argument("--once", action="store_true", help="待たずに今投票できる全レースを 1 回判定して終了")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("check", help="楽天競馬へのログイン・画面操作を確認（確定はしない）")
    s.add_argument("--headed", action="store_true", help="ブラウザを表示して Inspector で止める")
    s.add_argument("--race", help="確認画面まで進めるレースの RACEID")
    s.add_argument("--number", default="1", help="--race 時に単勝 100 円をセットする馬番")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("history", help="投票履歴")
    s.add_argument("--date")
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("set-credentials", help="楽天の ID・パスワード・暗証番号を資格情報マネージャーに保存")
    s.set_defaults(func=cmd_set_credentials)

    args = p.parse_args(argv)
    cfg = load_config(Path(args.config))
    _setup_logging(cfg, args.verbose)
    args.func(args, cfg)


if __name__ == "__main__":
    main()
