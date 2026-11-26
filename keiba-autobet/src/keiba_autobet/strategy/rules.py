"""YAML で書いた条件ルールを評価して買い目（Bet）を作る。

書き方は strategies/example.yaml と README を参照。
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from ..models import BET_SPEC, Bet, Entry, RaceCard, normalize_bet_type
from .scores import ScoreFunc, build_sources

log = logging.getLogger(__name__)

RESERVED_LEG_KEYS = {"sort", "top"}
OPS = {"eq", "in", "not_in", "min", "max", "contains", "not_contains", "source"}


# ---------------------------------------------------------------- 値の比較


def _norm(v: Any) -> Any:
    return v.replace("　", "").replace(" ", "") if isinstance(v, str) else v


def match_value(actual: Any, cond: Any) -> bool:
    """cond: スカラー（一致）/ リスト（いずれか一致）/ 演算子 dict。"""
    if isinstance(cond, dict):
        unknown = set(cond) - OPS
        if unknown:
            raise ValueError(f"不明な演算子: {', '.join(sorted(unknown))}")
        for op, v in cond.items():
            if op == "source":
                continue
            if op in ("min", "max"):
                if actual is None:
                    return False
                if op == "min" and actual < v:
                    return False
                if op == "max" and actual > v:
                    return False
            elif op == "eq" and not match_value(actual, v):
                return False
            elif op == "in" and not match_value(actual, list(v) if isinstance(v, (list, tuple)) else [v]):
                return False
            elif op == "not_in" and match_value(actual, list(v) if isinstance(v, (list, tuple)) else [v]):
                return False
            elif op in ("contains", "not_contains"):
                words = v if isinstance(v, list) else [v]
                hit = actual is not None and any(_norm(str(w)) in _norm(str(actual)) for w in words)
                if hit != (op == "contains"):
                    return False
        return True
    if isinstance(cond, (list, tuple)):
        return any(match_value(actual, c) for c in cond)
    if actual is None:
        return False
    if isinstance(cond, (int, float)) and not isinstance(cond, bool) and isinstance(actual, (int, float)):
        return float(actual) == float(cond)
    return _norm(actual) == _norm(cond)


# ---------------------------------------------------------------- フィールド


def race_fields(card: RaceCard) -> dict[str, Any]:
    r = card.race
    fav = card.favorite()
    active = card.active_entries
    second = sorted((e for e in active if e.popularity), key=lambda e: e.popularity)[1:2]
    return {
        "track": r.track,
        "race_no": r.race_no,
        "name": r.name,
        "surface": r.surface,
        "distance": r.distance,
        "runners": len(active) or r.runners,
        "weather": r.weather,
        "going": r.going,
        "post_hour": r.post_time.hour if r.post_time else None,
        "favorite_odds": fav.win_odds if fav else None,
        "favorite_place_odds": fav.place_odds_min if fav else None,
        "second_odds": second[0].win_odds if second else None,
    }


def entry_field(e: Entry, name: str) -> Any:
    aliases = {"place_odds": "place_odds_min", "belong": "jockey_belong"}
    name = aliases.get(name, name)
    if name in ("sex", "age"):
        return getattr(e, name)
    if not hasattr(e, name):
        raise ValueError(f"不明な馬の条件項目: {name}")
    return getattr(e, name)


# ---------------------------------------------------------------- 戦略


@dataclass
class Decision:
    strategy: str
    race_id: str
    matched: bool
    reason: str = ""
    bets: list[Bet] = field(default_factory=list)


@dataclass
class Strategy:
    name: str
    when_race: dict[str, Any]
    when_entry: dict[str, Any]
    select: dict[str, Any]
    bet: dict[str, Any]
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Strategy":
        if "name" not in d or "bet" not in d:
            raise ValueError(f"戦略には name と bet が必要です: {d}")
        when = d.get("when") or {}
        bet = dict(d["bet"])
        bet["type"] = normalize_bet_type(str(bet["type"]))
        amount = int(bet.get("amount", 100))
        if amount < 100 or amount % 100:
            raise ValueError(f"[{d['name']}] 金額は 100 円単位で指定してください: {amount}")
        bet["amount"] = amount
        combine = bet.setdefault("combine", "single" if BET_SPEC[bet["type"]][0] == 1 else "box")
        if combine not in ("single", "box", "nagashi", "formation"):
            raise ValueError(f"[{d['name']}] combine は single/box/nagashi/formation のいずれか: {combine}")
        if combine == "single" and BET_SPEC[bet["type"]][0] != 1:
            raise ValueError(f"[{d['name']}] {bet['type']} に combine: single は使えません")
        if combine == "formation" and len(bet.get("legs") or []) != BET_SPEC[bet["type"]][0]:
            raise ValueError(f"[{d['name']}] formation の legs は {BET_SPEC[bet['type']][0]} 個必要です")
        if combine == "nagashi" and not bet.get("axis"):
            raise ValueError(f"[{d['name']}] nagashi には axis（軸馬の条件）が必要です")
        return cls(
            name=str(d["name"]),
            when_race=when.get("race") or {},
            when_entry=when.get("entry") or {},
            select=d.get("select") or {},
            bet=bet,
            enabled=bool(d.get("enabled", True)),
        )

    # -- 候補馬の抽出

    def _filter(self, entries: list[Entry], cond: dict[str, Any], scores: dict[str, dict[int, float]]) -> list[Entry]:
        out = []
        for e in entries:
            ok = True
            for key, c in cond.items():
                if key in RESERVED_LEG_KEYS:
                    continue
                if key == "score":
                    src = c.get("source") if isinstance(c, dict) else None
                    if not src:
                        raise ValueError(f"[{self.name}] score 条件には source が必要です")
                    actual = scores.get(src, {}).get(e.number)
                else:
                    actual = entry_field(e, key)
                if not match_value(actual, c):
                    ok = False
                    break
            if ok:
                out.append(e)
        return _sort_top(out, cond.get("sort"), cond.get("top"), scores)

    def evaluate(self, card: RaceCard, score_sources: dict[str, ScoreFunc]) -> Decision:
        rid = card.race.race_id
        if not self.enabled:
            return Decision(self.name, rid, False, "無効化されています")
        rf = race_fields(card)
        for key, c in self.when_race.items():
            if key not in rf:
                raise ValueError(f"[{self.name}] 不明なレース条件項目: {key}")
            if not match_value(rf[key], c):
                return Decision(self.name, rid, False, f"レース条件 {key}={rf[key]!r} が不一致")

        scores: dict[str, dict[int, float]] = {}
        for name, fn in score_sources.items():
            try:
                scores[name] = {int(k): float(v) for k, v in (fn(card) or {}).items()}
            except Exception as ex:  # スコアの失敗で全体を止めない
                log.warning("スコア %s の計算に失敗: %s", name, ex)
                scores[name] = {}

        active = card.active_entries
        cands = self._filter(active, self.when_entry, scores)
        cands = _sort_top(cands, self.select.get("sort"), self.select.get("top"), scores)
        lo = self.select.get("min_candidates", 1)
        hi = self.select.get("max_candidates")
        if len(cands) < lo:
            return Decision(self.name, rid, False, f"条件に合う馬が {len(cands)} 頭（必要 {lo} 頭以上）")
        if hi is not None and len(cands) > hi:
            return Decision(self.name, rid, False, f"条件に合う馬が {len(cands)} 頭（上限 {hi} 頭）")

        try:
            combos = self._combos(active, cands, scores)
        except _NoBet as ex:
            return Decision(self.name, rid, False, str(ex))
        max_points = int(self.bet.get("max_points", 100))
        if not combos:
            return Decision(self.name, rid, False, "買い目が 0 点")
        if len(combos) > max_points:
            return Decision(self.name, rid, False, f"買い目が {len(combos)} 点で max_points={max_points} を超過")
        bets = [Bet(rid, self.name, self.bet["type"], c, self.bet["amount"]) for c in combos]
        names = ", ".join(f"{e.number}{e.horse}" for e in cands)
        return Decision(self.name, rid, True, f"候補: {names}", bets)

    # -- 組合せ

    def _combos(self, active: list[Entry], cands: list[Entry], scores) -> list[tuple[int, ...]]:
        n, ordered, by_frame = BET_SPEC[self.bet["type"]]
        unit: Callable[[Entry], int] = (lambda e: e.frame) if by_frame else (lambda e: e.number)
        combine = self.bet["combine"]

        def units(es: list[Entry]) -> list[int]:
            return list(dict.fromkeys(unit(e) for e in es))

        def leg(spec: Any) -> list[int]:
            if spec in (None, "candidates", "候補"):
                return units(cands)
            if spec in ("all", "全"):
                return units(active)
            return units(self._filter(active, spec, scores))

        if combine == "single":
            return [(u,) for u in units(cands)]
        if combine == "box":
            pool = units(cands)
            if len(pool) < n:
                raise _NoBet(f"ボックスに必要な {n} 頭（枠）に足りません: {len(pool)}")
            gen = itertools.permutations(pool, n) if ordered else itertools.combinations(pool, n)
            return [tuple(c) if ordered else tuple(sorted(c)) for c in gen]
        if combine == "nagashi":
            axis = leg(self.bet["axis"])
            if not axis:
                raise _NoBet("軸馬が見つかりません")
            partners = [u for u in leg(self.bet.get("partners", "candidates")) if u not in axis or by_frame]
            legs = [axis] + [partners] * (n - 1)
            combos = _product(legs, ordered, by_frame)
            if ordered and self.bet.get("multi"):
                combos = list(dict.fromkeys(p for c in combos for p in itertools.permutations(c)))
            return combos
        legs = [leg(s) for s in self.bet["legs"]]
        return _product(legs, ordered, by_frame)


class _NoBet(Exception):
    pass


def _product(legs: list[list[int]], ordered: bool, by_frame: bool) -> list[tuple[int, ...]]:
    out: dict[tuple[int, ...], None] = {}
    for c in itertools.product(*legs):
        if len(set(c)) != len(c) and not by_frame:  # 枠番の券種は同じ枠（ゾロ目）を許可
            continue
        out[c if ordered else tuple(sorted(c))] = None
    return list(out)


def _sort_top(es: list[Entry], sort: str | None, top: int | None, scores) -> list[Entry]:
    if sort:
        desc = sort.startswith("-")
        key = sort.lstrip("-")
        if key.startswith("score"):
            src = key.partition(":")[2]
            if not src:
                raise ValueError("score で並べる場合は 'score:ソース名' と書いてください")
            getter = lambda e: scores.get(src, {}).get(e.number)  # noqa: E731
        else:
            getter = lambda e: entry_field(e, key)  # noqa: E731
        present = [e for e in es if getter(e) is not None]
        missing = [e for e in es if getter(e) is None]
        es = sorted(present, key=getter, reverse=desc) + missing
    if top is not None:
        es = es[: int(top)]
    return es


# ---------------------------------------------------------------- 読み込み


@dataclass
class StrategySet:
    strategies: list[Strategy]
    score_sources: dict[str, ScoreFunc]

    def evaluate(self, card: RaceCard) -> list[Decision]:
        return [s.evaluate(card, self.score_sources) for s in self.strategies]


def load_strategies(path: str | Path) -> StrategySet:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    strategies = [Strategy.from_dict(d) for d in data.get("strategies") or []]
    names = [s.name for s in strategies]
    if len(names) != len(set(names)):
        raise ValueError("戦略名（name）が重複しています")
    return StrategySet(strategies, build_sources(data.get("scores"), base_dir=path.parent))
