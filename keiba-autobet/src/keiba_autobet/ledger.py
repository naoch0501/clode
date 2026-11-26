"""投票履歴を SQLite に記録する。上限計算と二重投票防止にも使う。"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from .models import JST, Bet

SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  race_date TEXT NOT NULL,
  race_id TEXT NOT NULL,
  strategy TEXT NOT NULL,
  bet_type TEXT NOT NULL,
  selection TEXT NOT NULL,
  amount INTEGER NOT NULL,
  mode TEXT NOT NULL,          -- dry / live
  status TEXT NOT NULL,        -- placed / rejected / failed
  reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS bets_day ON bets(race_date, mode, status);
CREATE INDEX IF NOT EXISTS bets_race ON bets(race_id, mode, status);
"""


class Ledger:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def record(self, bet: Bet, mode: str, status: str, reason: str = "") -> None:
        race_date = f"{bet.race_id[:4]}-{bet.race_id[4:6]}-{bet.race_id[6:8]}"
        self.db.execute(
            "INSERT INTO bets(created_at, race_date, race_id, strategy, bet_type, selection, amount, mode, status, reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(JST).isoformat(timespec="seconds"),
                race_date,
                bet.race_id,
                bet.strategy,
                bet.bet_type,
                bet.selection_str,
                bet.amount,
                mode,
                status,
                reason,
            ),
        )
        self.db.commit()

    def spent_on_day(self, day: date, mode: str) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bets WHERE race_date=? AND mode=? AND status='placed'",
            (day.isoformat(), mode),
        ).fetchone()
        return int(row[0])

    def spent_on_race(self, race_id: str, mode: str) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bets WHERE race_id=? AND mode=? AND status='placed'",
            (race_id, mode),
        ).fetchone()
        return int(row[0])

    def already_bet(self, race_id: str, strategy: str, mode: str) -> bool:
        """同じレース・同じ戦略で投票済みか（失敗も含めて再試行しない）。"""
        row = self.db.execute(
            "SELECT 1 FROM bets WHERE race_id=? AND strategy=? AND mode=? AND status IN ('placed','failed') LIMIT 1",
            (race_id, strategy, mode),
        ).fetchone()
        return row is not None

    def history(self, day: date | None = None, limit: int = 200) -> list[sqlite3.Row]:
        if day:
            return self.db.execute(
                "SELECT * FROM bets WHERE race_date=? ORDER BY id DESC LIMIT ?", (day.isoformat(), limit)
            ).fetchall()
        return self.db.execute("SELECT * FROM bets ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
