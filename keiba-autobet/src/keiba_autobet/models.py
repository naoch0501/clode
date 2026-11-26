"""データモデル。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

# 券種の正式名（楽天競馬の表記）と別名
BET_TYPES = ["単勝", "複勝", "枠複", "枠単", "馬複", "馬単", "ワイド", "3連複", "3連単"]
_BET_ALIASES = {
    "枠連": "枠複",
    "馬連": "馬複",
    "三連複": "3連複",
    "三連単": "3連単",
    "３連複": "3連複",
    "３連単": "3連単",
}
# 券種ごとの選択頭数・順序の有無・枠番で選ぶか
BET_SPEC: dict[str, tuple[int, bool, bool]] = {
    "単勝": (1, True, False),
    "複勝": (1, True, False),
    "枠複": (2, False, True),
    "枠単": (2, True, True),
    "馬複": (2, False, False),
    "馬単": (2, True, False),
    "ワイド": (2, False, False),
    "3連複": (3, False, False),
    "3連単": (3, True, False),
}


def normalize_bet_type(name: str) -> str:
    name = name.strip()
    name = _BET_ALIASES.get(name, name)
    if name not in BET_SPEC:
        raise ValueError(f"未対応の券種です: {name}（対応: {', '.join(BET_TYPES)}）")
    return name


@dataclass
class Race:
    race_id: str
    date: date
    track: str
    race_no: int
    name: str = ""
    post_time: datetime | None = None
    surface: str = ""  # ダ / 芝
    distance: int | None = None
    runners: int | None = None
    weather: str = ""
    going: str = ""  # 良 / 稍重 / 重 / 不良
    status: str = ""  # 発売中 / 確定 など（一覧ページの投票欄）

    @property
    def track_code(self) -> str:
        return self.race_id[8:10]


@dataclass
class Entry:
    frame: int
    number: int
    horse: str
    horse_id: str = ""
    sex_age: str = ""
    burden: float | None = None  # 負担重量
    jockey: str = ""
    jockey_belong: str = ""
    trainer: str = ""
    win_odds: float | None = None
    place_odds_min: float | None = None
    place_odds_max: float | None = None
    popularity: int | None = None
    scratched: bool = False
    score: float | None = None  # 独自スコア（strategy 評価時に付与）

    @property
    def sex(self) -> str:
        return self.sex_age[:1]

    @property
    def age(self) -> int | None:
        digits = "".join(c for c in self.sex_age if c.isdigit())
        return int(digits) if digits else None


@dataclass
class RaceCard:
    race: Race
    entries: list[Entry] = field(default_factory=list)
    fetched_at: datetime | None = None

    @property
    def active_entries(self) -> list[Entry]:
        return [e for e in self.entries if not e.scratched]

    def favorite(self) -> Entry | None:
        cands = [e for e in self.active_entries if e.popularity is not None]
        return min(cands, key=lambda e: e.popularity) if cands else None


@dataclass
class Bet:
    race_id: str
    strategy: str
    bet_type: str
    selection: tuple[int, ...]  # 馬番（枠複・枠単は枠番）
    amount: int

    @property
    def selection_str(self) -> str:
        sep = ">" if BET_SPEC[self.bet_type][1] and len(self.selection) > 1 else "-"
        return sep.join(str(n) for n in self.selection)

    def __str__(self) -> str:
        return f"{self.race_id} {self.bet_type} {self.selection_str} {self.amount}円 [{self.strategy}]"
