"""投票先のインターフェース。SPAT4 等を追加する場合はこれを実装する。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import Bet


class HaltError(Exception):
    """以降の投票を全て止めるべき異常（残高不一致など）。"""


@dataclass
class PlaceResult:
    ok: bool
    message: str = ""


class Voter(Protocol):
    mode: str  # "dry" / "live"

    def start(self) -> None: ...

    def balance(self) -> int | None:
        """購入可能残高（円）。取得できない場合は None。"""
        ...

    def place(self, bets: list[Bet]) -> PlaceResult:
        """1 レース分の買い目をまとめて投票する。"""
        ...

    def close(self) -> None: ...
