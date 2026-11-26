import pytest

from keiba_autobet.strategy.rules import Strategy, match_value

# 名古屋11R のオッズ: 1人気=11番(2.4) 2人気=7番(3.3) 3人気=8番(5.4) 4人気=9番 5人気=6番


def ev(card, d, sources=None):
    return Strategy.from_dict(d).evaluate(card, sources or {})


def test_match_value():
    assert match_value(3, 3)
    assert match_value("大井", ["大井", "川崎"])
    assert not match_value("船橋", ["大井", "川崎"])
    assert match_value(2.5, {"min": 2.0, "max": 3.0})
    assert not match_value(None, {"min": 2.0})
    assert match_value("森泰斗", {"contains": "森泰"})
    assert match_value("名古屋", {"not_in": ["大井"]})
    with pytest.raises(ValueError):
        match_value(1, {"gte": 1})


def test_single_win(card):
    d = ev(card, {"name": "s", "when": {"entry": {"popularity": 1}}, "bet": {"type": "単勝", "amount": 300}})
    assert d.matched
    assert [(b.bet_type, b.selection, b.amount) for b in d.bets] == [("単勝", (11,), 300)]


def test_race_condition_reject(card):
    d = ev(card, {"name": "s", "when": {"race": {"track": "大井"}}, "bet": {"type": "複勝"}})
    assert not d.matched and "track" in d.reason


def test_race_fields(card):
    d = ev(card, {"name": "s", "when": {"race": {"surface": "ダ", "distance": {"min": 1600},
                                                  "favorite_odds": {"max": 2.5}, "runners": 12},
                                         "entry": {"popularity": 2}}, "bet": {"type": "複勝"}})
    assert d.matched and d.bets[0].selection == (7,)


def test_box_umafuku(card):
    d = ev(card, {"name": "s", "when": {"entry": {"popularity": {"max": 3}}},
                  "bet": {"type": "馬連", "combine": "box", "amount": 100}})
    assert sorted(b.selection for b in d.bets) == [(7, 8), (7, 11), (8, 11)]
    assert d.bets[0].bet_type == "馬複"


def test_box_umatan_ordered(card):
    d = ev(card, {"name": "s", "when": {"entry": {"popularity": {"max": 3}}},
                  "bet": {"type": "馬単", "combine": "box"}})
    assert len(d.bets) == 6


def test_nagashi_wide(card):
    d = ev(card, {"name": "s", "when": {"entry": {"popularity": {"min": 2, "max": 4}}},
                  "bet": {"type": "ワイド", "combine": "nagashi", "axis": {"popularity": 1}}})
    assert sorted(b.selection for b in d.bets) == [(7, 11), (8, 11), (9, 11)]


def test_nagashi_sanrentan_multi(card):
    d = ev(card, {"name": "s", "when": {"entry": {"popularity": {"min": 2, "max": 3}}},
                  "bet": {"type": "三連単", "combine": "nagashi", "axis": {"popularity": 1}, "multi": True}})
    assert len(d.bets) == 6  # 11-7-8 のマルチ
    d = ev(card, {"name": "s", "when": {"entry": {"popularity": {"min": 2, "max": 3}}},
                  "bet": {"type": "三連単", "combine": "nagashi", "axis": {"popularity": 1}}})
    assert sorted(b.selection for b in d.bets) == [(11, 7, 8), (11, 8, 7)]


def test_formation_and_max_points(card):
    base = {"name": "s", "when": {"entry": {"popularity": {"max": 5}}},
            "bet": {"type": "3連単", "combine": "formation",
                    "legs": [{"popularity": 1}, {"popularity": {"max": 3}}, "candidates"]}}
    d = ev(card, base)
    assert len(d.bets) == 2 * 3  # 1着11 / 2着 7,8 / 3着 残り3頭
    assert all(b.selection[0] == 11 and len(set(b.selection)) == 3 for b in d.bets)
    base["bet"]["max_points"] = 5
    assert not ev(card, base).matched


def test_frame_bet(card):
    d = ev(card, {"name": "s", "when": {"entry": {"popularity": {"max": 2}}},
                  "bet": {"type": "枠連", "combine": "box"}})
    assert [b.selection for b in d.bets] == [(6, 8)]


def test_score_source_and_sort(card):
    scores = {"m": lambda c: {1: 90, 2: 80, 3: 10}}
    d = ev(card, {"name": "s", "when": {"entry": {"score": {"source": "m", "min": 50}}},
                  "select": {"sort": "-score:m", "top": 1}, "bet": {"type": "単勝"}}, scores)
    assert [b.selection for b in d.bets] == [(1,)]


def test_min_candidates(card):
    d = ev(card, {"name": "s", "when": {"entry": {"popularity": 1}}, "select": {"min_candidates": 2},
                  "bet": {"type": "単勝"}})
    assert not d.matched


def test_validation():
    with pytest.raises(ValueError):
        Strategy.from_dict({"name": "s", "bet": {"type": "単勝", "amount": 150}})
    with pytest.raises(ValueError):
        Strategy.from_dict({"name": "s", "bet": {"type": "馬複", "combine": "single"}})
    with pytest.raises(ValueError):
        Strategy.from_dict({"name": "s", "bet": {"type": "WIN5"}})


def test_example_yaml_loads():
    from pathlib import Path

    from keiba_autobet.strategy.rules import load_strategies

    ss = load_strategies(Path(__file__).parents[1] / "strategies" / "example.yaml")
    assert len(ss.strategies) == 5
