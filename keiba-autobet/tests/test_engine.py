from datetime import datetime, timedelta

import pytest

from keiba_autobet.config import Config, Limits
from keiba_autobet.ledger import Ledger
from keiba_autobet.models import JST, Bet
from keiba_autobet.risk import RiskManager
from keiba_autobet.scheduler import Engine
from keiba_autobet.strategy.rules import Strategy, StrategySet
from keiba_autobet.voter.base import HaltError, PlaceResult
from keiba_autobet.voter.dryrun import DryRunVoter

POST = datetime(2026, 11, 26, 20, 15, tzinfo=JST)


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "l.db")


def bets(card, n=1, amount=100, strategy="s"):
    return [Bet(card.race.race_id, strategy, "単勝", (i + 1,), amount) for i in range(n)]


def test_risk_limits(card, ledger):
    rm = RiskManager(Limits(max_per_bet=500, max_per_race=1000, max_per_day=1500, min_balance=200),
                     ledger, "dry", 2)
    now = POST - timedelta(minutes=10)
    assert rm.check(card.race, "s", bets(card, 2, 300), now=now).ok
    assert "max_per_bet" in rm.check(card.race, "s", bets(card, 1, 600), now=now).reason
    assert "max_per_race" in rm.check(card.race, "s", bets(card, 4, 300), now=now).reason
    assert "締切" in rm.check(card.race, "s", bets(card), now=POST - timedelta(minutes=1)).reason
    assert "min_balance" in rm.check(card.race, "s", bets(card, 1, 300), balance=400, now=now).reason
    for b in bets(card, 2, 500):
        ledger.record(b, "dry", "placed")
    assert "投票済み" in rm.check(card.race, "s", bets(card), now=now).reason
    assert "max_per_day" in rm.check(card.race, "t", bets(card, 1, 100), now=now).reason \
        or "max_per_race" in rm.check(card.race, "t", bets(card, 1, 100), now=now).reason
    # live の履歴とは別勘定
    assert RiskManager(Limits(), ledger, "live", 2).check(card.race, "s", bets(card), now=now).ok


class FakeSource:
    def __init__(self, card):
        self.card = card
        self.calls = 0

    def list_races(self, day):
        return [self.card.race]

    def get_card(self, race_id):
        self.calls += 1
        return self.card


def make_engine(card, ledger, voter=None, now=POST - timedelta(minutes=4)):
    ss = StrategySet([
        Strategy.from_dict({"name": "fav", "when": {"entry": {"popularity": 1}}, "bet": {"type": "複勝", "amount": 500}}),
        Strategy.from_dict({"name": "box", "when": {"entry": {"popularity": {"max": 3}}},
                            "bet": {"type": "馬複", "combine": "box", "amount": 1000}}),
    ], {})
    cfg = Config(limits=Limits(max_per_bet=1000, max_per_race=2000, max_per_day=5000))
    clock = {"t": now}
    eng = Engine(cfg, FakeSource(card), ss, voter or DryRunVoter(), ledger,
                 clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + timedelta(seconds=s)))
    return eng


def test_engine_places_within_limits(card, ledger):
    eng = make_engine(card, ledger)
    out = eng.run_day(card.race.date)
    assert [b.strategy for b in out[0].placed] == ["fav"]  # box(3000円)はレース上限で拒否
    assert out[0].rejected[0][0] == "box"
    assert ledger.spent_on_race(card.race.race_id, "dry") == 500
    # 2 回目は投票済みで買わない
    out = make_engine(card, ledger).run_day(card.race.date)
    assert out[0].placed == []


def test_engine_waits_until_lead(card, ledger):
    eng = make_engine(card, ledger, now=POST - timedelta(hours=1))
    out = eng.run_day(card.race.date)
    assert len(out) == 1 and out[0].placed
    assert eng.clock() >= POST - timedelta(minutes=5)


def test_engine_skips_after_cutoff(card, ledger):
    eng = make_engine(card, ledger, now=POST - timedelta(minutes=1))
    assert eng.run_day(card.race.date) == []


class HaltVoter(DryRunVoter):
    mode = "live"

    def place(self, bets):
        raise HaltError("残高不一致")


class FailVoter(DryRunVoter):
    def place(self, bets):
        return PlaceResult(False, "ng")


def test_engine_halt(card, ledger):
    with pytest.raises(HaltError):
        make_engine(card, ledger, HaltVoter()).run_day(card.race.date)
    rows = ledger.history()
    assert any(r["status"] == "failed" for r in rows)


def test_engine_failed_not_retried(card, ledger):
    make_engine(card, ledger, FailVoter()).run_day(card.race.date)
    assert ledger.spent_on_race(card.race.race_id, "dry") == 0
    assert ledger.already_bet(card.race.race_id, "fav", "dry")
