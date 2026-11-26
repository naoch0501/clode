from pathlib import Path

import pytest

from keiba_autobet.datasource.rakuten import parse_odds_tanfuku

FIX = Path(__file__).parent / "fixtures"
RACE_ID = "202611262433170311"  # 名古屋 11R（2026/11/26 保存）


@pytest.fixture
def card():
    return parse_odds_tanfuku((FIX / "odds_tanfuku.html").read_text(encoding="utf-8"), RACE_ID)
