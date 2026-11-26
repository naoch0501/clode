"""楽天競馬へ Playwright（Chromium）で投票する。

画面操作の手順とセレクタは selectors_rakuten.yaml に書いてあり、コードには持たない。
ログイン後の投票画面は事前に確認できていないため、初回は `keiba-autobet check --headed`
で実際の画面を見ながら YAML を確定させること（README 参照）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .. import credentials
from ..models import JST, Bet
from .base import HaltError, PlaceResult
from .steps import StepError, StepRunner

log = logging.getLogger(__name__)

DEFAULT_SELECTORS = Path(__file__).with_name("selectors_rakuten.yaml")


def _yen(text: str) -> int | None:
    m = re.search(r"[\d,]+", text or "")
    return int(m.group().replace(",", "")) if m else None


class RakutenVoter:
    mode = "live"

    def __init__(
        self,
        selectors_path: str | Path | None = None,
        profile_dir: str | Path = "data/browser-profile",
        shot_dir: str | Path = "logs/screenshots",
        headless: bool = False,
        submit: bool = True,
        slow_mo_ms: int = 100,
        channel: str | None = None,
        executable_path: str | None = None,
    ):
        path = Path(selectors_path) if selectors_path else DEFAULT_SELECTORS
        self.flows: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.profile_dir = Path(profile_dir)
        self.shot_dir = Path(shot_dir)
        self.headless = headless
        self.submit = submit  # False なら最終確定ボタンを押さない（check 用）
        self.slow_mo_ms = slow_mo_ms
        self.launch_opts = {k: v for k, v in {"channel": channel, "executable_path": executable_path}.items() if v}
        self._pw = self._ctx = self.page = None
        self._runner: StepRunner | None = None
        self._creds: dict[str, str] = {}

    # -- ライフサイクル

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as ex:
            raise RuntimeError(
                "Playwright が入っていません: pip install -e .[live] && python -m playwright install chromium"
            ) from ex
        self._creds = credentials.load()
        self._pw = sync_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # 永続プロファイルでログイン状態を保持（2段階認証の頻度を減らす）
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir), headless=self.headless, slow_mo=self.slow_mo_ms, locale="ja-JP",
            **self.launch_opts,
        )
        self._ctx.set_default_timeout(30_000)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._runner = StepRunner(self.page, self.shot_dir)
        self.login()

    def close(self) -> None:
        if self._ctx:
            self._ctx.close()
        if self._pw:
            self._pw.stop()
        self._ctx = self._pw = self.page = None

    # -- 操作

    def _vars(self, **extra: Any) -> dict[str, Any]:
        return {**(self.flows.get("urls") or {}), **self._creds, **extra}

    def _run(self, flow: str, **extra: Any) -> dict[str, Any]:
        assert self._runner, "start() を先に呼んでください"
        return self._runner.run(self.flows.get(flow) or [], self._vars(**extra), flow)

    def is_logged_in(self) -> bool:
        try:
            self._run("logged_in_check")
            return True
        except StepError:
            return False

    def login(self) -> None:
        if self.flows.get("logged_in_check") and self.is_logged_in():
            log.info("楽天競馬: ログイン済み")
            return
        log.info("楽天競馬: ログインします")
        self._run("login")

    def balance(self) -> int | None:
        if not self.flows.get("balance"):
            return None
        try:
            v = self._run("balance").get("balance", "")
        except StepError as ex:
            log.warning("残高を取得できません: %s", ex)
            return None
        return _yen(v)

    def place(self, bets: list[Bet]) -> PlaceResult:
        if not bets:
            return PlaceResult(False, "買い目なし")
        race_id = bets[0].race_id
        if any(b.race_id != race_id for b in bets):
            raise ValueError("place() は 1 レース分ずつ呼んでください")
        total = sum(b.amount for b in bets)
        before = self.balance()
        submitted = False
        try:
            self._run("open_race", race_id=race_id, race_no=int(race_id[-2:]))
            for b in bets:
                self._run(
                    "add_bet",
                    race_id=race_id,
                    bet_type=b.bet_type,
                    selection=list(b.selection),
                    amount=b.amount,
                    units=b.amount // 100,
                    strategy=b.strategy,
                )
            self._run("confirm", total=total, total_fmt=f"{total:,}", points=len(bets))
            stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
            self._runner.run([{"screenshot": f"confirm_{race_id}_{stamp}"}], {}, "shot")
            if not self.submit:
                return PlaceResult(False, "submit=False のため確定せずに終了（確認画面まで）")
            submitted = True  # ここから先の失敗は「買えたかどうか不明」扱い
            self._run("submit", total=total, total_fmt=f"{total:,}", points=len(bets))
            self._runner.run([{"screenshot": f"done_{race_id}_{stamp}"}], {}, "shot")
        except StepError as ex:
            if not submitted:
                return PlaceResult(False, f"投票前の操作で失敗（未購入）: {ex}")
            # 確定ボタンを押したかどうか不明なので、残高で確認できない場合は停止する
            after = self.balance()
            if before is not None and after is not None and before - after == total:
                raise HaltError(f"エラーが出ましたが残高は {total} 円減っています。履歴を手動で確認してください: {ex}")
            if before is not None and after is not None and before == after:
                return PlaceResult(False, f"投票失敗（残高変化なし）: {ex}")
            raise HaltError(f"投票の成否が確認できません。手動で投票履歴を確認してください: {ex}") from ex

        after = self.balance()
        if before is not None and after is not None and before - after != total:
            raise HaltError(f"残高の減少 {before - after} 円が投票額 {total} 円と一致しません")
        return PlaceResult(True, f"投票完了 {len(bets)} 点 / {total} 円（残高 {after} 円）")
