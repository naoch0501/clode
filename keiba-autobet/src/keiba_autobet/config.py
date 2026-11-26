"""config.yaml の読み込み。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Limits:
    max_per_bet: int = 1000  # 1 点あたり
    max_per_race: int = 3000  # 1 レースあたり（全戦略合計）
    max_per_day: int = 10000  # 1 日あたり
    min_balance: int = 0  # 投票後の残高がこれを下回るなら投票しない
    max_points_per_race: int = 50  # 1 レースあたりの点数


@dataclass
class Schedule:
    lead_minutes: float = 5.0  # 発走何分前にオッズを取得して判定するか
    cutoff_minutes: float = 2.0  # 発走何分前を過ぎたら投票しないか
    poll_seconds: float = 30.0


@dataclass
class Config:
    live_enabled: bool = False  # true かつ --live のときだけ実投票
    limits: Limits = field(default_factory=Limits)
    schedule: Schedule = field(default_factory=Schedule)
    tracks: list[str] = field(default_factory=list)  # 空なら全競馬場
    ledger_path: str = "data/ledger.sqlite3"
    log_dir: str = "logs"
    webhook_url: str = ""  # Discord / Slack 互換 Webhook（任意）
    voter: dict = field(default_factory=dict)  # 楽天投票の設定（headless など）
    base_dir: Path = Path(".")

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else self.base_dir / path


def load_config(path: str | Path | None) -> Config:
    if path is None or not Path(path).exists():
        cfg = Config()
        if path is not None:
            cfg.base_dir = Path(path).parent
        return cfg
    path = Path(path)
    d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    limits = Limits(**(d.pop("limits", None) or {}))
    schedule = Schedule(**(d.pop("schedule", None) or {}))
    for k in ("max_per_bet", "max_per_race", "max_per_day"):
        if getattr(limits, k) <= 0:
            raise ValueError(f"limits.{k} は 1 以上にしてください")
    if schedule.cutoff_minutes < 1:
        raise ValueError("schedule.cutoff_minutes は 1 分以上にしてください")
    known = set(Config.__dataclass_fields__) - {"limits", "schedule", "base_dir"}
    unknown = set(d) - known
    if unknown:
        raise ValueError(f"config に不明な項目があります: {', '.join(sorted(unknown))}")
    return Config(limits=limits, schedule=schedule, base_dir=path.parent, **d)
