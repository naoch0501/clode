"""データ取得元のインターフェース。別のサイトや JRA-VAN 等に差し替える場合はこれを実装する。"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from ..models import Race, RaceCard


class DataSource(Protocol):
    def list_races(self, day: date) -> list[Race]:
        """指定日に開催される全競馬場の全レース（発走時刻順）。"""
        ...

    def get_card(self, race_id: str) -> RaceCard:
        """出走馬と最新の単勝・複勝オッズ・人気。"""
        ...
