"""独自予想スコアの読み込み。

戦略 YAML の `scores:` で定義し、条件で `score: {source: 名前, min: 70}` のように使う。

  scores:
    my_csv:                       # CSV: race_id,number,score の列を持つファイル
      csv: data/scores.csv
    my_model:                     # Python 関数: (RaceCard) -> {馬番: スコア}
      python: my_package.my_module:predict
      path: ./plugins             # 任意: import 前に sys.path に追加するフォルダ
"""

from __future__ import annotations

import csv
import importlib
import sys
from pathlib import Path
from typing import Callable

from ..models import RaceCard

ScoreFunc = Callable[[RaceCard], dict[int, float]]


class CsvScores:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._mtime = 0.0
        self._data: dict[str, dict[int, float]] = {}

    def _load(self) -> None:
        if not self.path.exists():
            self._data, self._mtime = {}, 0.0
            return
        mtime = self.path.stat().st_mtime
        if mtime == self._mtime:
            return
        data: dict[str, dict[int, float]] = {}
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                data.setdefault(row["race_id"].strip(), {})[int(row["number"])] = float(row["score"])
        self._data, self._mtime = data, mtime

    def __call__(self, card: RaceCard) -> dict[int, float]:
        self._load()  # 当日中に CSV を更新しても反映されるよう毎回 mtime を確認
        return self._data.get(card.race.race_id, {})


def load_python(spec: str, path: str | None = None, base_dir: Path | None = None) -> ScoreFunc:
    if path:
        p = Path(path)
        if not p.is_absolute() and base_dir:
            p = base_dir / p
        sys.path.insert(0, str(p.resolve()))
    module_name, _, func_name = spec.partition(":")
    if not func_name:
        raise ValueError(f"python スコアは 'module:function' 形式で指定してください: {spec}")
    return getattr(importlib.import_module(module_name), func_name)


def build_sources(conf: dict | None, base_dir: Path | None = None) -> dict[str, ScoreFunc]:
    sources: dict[str, ScoreFunc] = {}
    for name, c in (conf or {}).items():
        if "csv" in c:
            p = Path(c["csv"])
            sources[name] = CsvScores(p if p.is_absolute() or not base_dir else base_dir / p)
        elif "python" in c:
            sources[name] = load_python(c["python"], c.get("path"), base_dir)
        else:
            raise ValueError(f"スコア '{name}' には csv か python を指定してください")
    return sources
