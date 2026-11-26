"""模擬投票。実際には何も買わず、ログに出すだけ。"""

from __future__ import annotations

import logging

from ..models import Bet
from .base import PlaceResult

log = logging.getLogger(__name__)


class DryRunVoter:
    mode = "dry"

    def __init__(self, balance: int | None = None):
        self._balance = balance

    def start(self) -> None:
        log.info("模擬投票モード（実際には投票しません）")

    def balance(self) -> int | None:
        return self._balance

    def place(self, bets: list[Bet]) -> PlaceResult:
        for b in bets:
            log.info("[模擬投票] %s", b)
        if self._balance is not None:
            self._balance -= sum(b.amount for b in bets)
        return PlaceResult(True, f"模擬投票 {len(bets)} 点 / {sum(b.amount for b in bets)} 円")

    def close(self) -> None:
        pass
