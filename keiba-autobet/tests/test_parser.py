from datetime import datetime

from keiba_autobet.datasource.rakuten import parse_day_index, parse_venue_races
from keiba_autobet.models import JST

from .conftest import FIX


def test_day_index():
    ids = parse_day_index((FIX / "day_index.html").read_text(encoding="utf-8"))
    assert ids == ["202611261813080300", "202611262218150200", "202611262433170300", "202611262726190200"]


def test_venue_races():
    races = parse_venue_races((FIX / "venue_races.html").read_text(encoding="utf-8"))
    assert len(races) == 12
    r = races[0]
    assert r.race_id == "202611262433170301"
    assert r.track == "名古屋"
    assert r.race_no == 1
    assert r.post_time == datetime(2026, 11, 26, 14, 40, tzinfo=JST)
    assert (r.surface, r.distance, r.runners) == ("ダ", 1500, 12)


def test_odds_card(card):
    r = card.race
    assert (r.track, r.race_no, r.surface, r.distance) == ("名古屋", 11, "ダ", 1700)
    assert (r.weather, r.going) == ("晴", "稍重")
    assert r.post_time == datetime(2026, 11, 26, 20, 15, tzinfo=JST)
    assert "ゴールドウィング賞" in r.name
    assert len(card.entries) == 12
    e = card.entries[0]
    assert (e.frame, e.number, e.horse, e.jockey, e.trainer) == (1, 1, "モズレディーラック", "木之葵", "迫田清")
    assert (e.win_odds, e.place_odds_min, e.place_odds_max, e.popularity) == (55.0, 6.8, 16.3, 10)
    assert (e.sex, e.age, e.burden) == ("牝", 2, 55.0)
    assert card.favorite().number == 11
    assert card.entries[2].jockey_belong == "笠松"
