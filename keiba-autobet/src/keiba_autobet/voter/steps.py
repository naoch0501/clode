"""YAML に書いたブラウザ操作手順を Playwright で実行する小さなランナー。

サイトの画面が変わったときにコードを直さず YAML だけ直せるようにするためのもの。

使えるアクション（1 ステップ = 1 つの dict）:
  goto: URL
  click: セレクタ               （"text=投票する" のような Playwright のセレクタ）
  fill: {selector, value}
  select: {selector, value}     （<select> の value / label）
  check: セレクタ               （チェックボックス・ラジオ）
  press: {selector, key}
  wait_for: セレクタ  / {selector, timeout_sec}
  wait_ms: ミリ秒
  read_text: {selector, var}    （テキストを変数に保存。balance など）
  expect_text: 文字列           （画面にこの文字列が無ければ中止）
  screenshot: ファイル名
  pause: true                  （headed のとき Playwright Inspector で一時停止）
  for_each_leg: [ステップ...]   （買い目の 1 頭ずつ、{pos}=何頭目(1始まり) {n}=馬番 で繰り返す）
  optional: [ステップ...]       （失敗しても続行するステップ群）

値の中の {name} は変数で置き換わる（例: {race_id} {bet_type} {amount} {user_id}）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class StepError(Exception):
    pass


class _Vars(dict):
    def __missing__(self, key: str) -> str:
        raise StepError(f"未定義の変数 {{{key}}} が手順に含まれています")


def _fmt(v: Any, vars: dict[str, Any]) -> Any:
    return v.format_map(_Vars(vars)) if isinstance(v, str) else v


class StepRunner:
    def __init__(self, page, shot_dir: Path, secret_keys: tuple[str, ...] = ("password", "pin")):
        self.page = page
        self.shot_dir = shot_dir
        self.secret_keys = secret_keys

    def run(self, steps: list[dict[str, Any]], vars: dict[str, Any], label: str = "") -> dict[str, Any]:
        for i, step in enumerate(steps or []):
            if not isinstance(step, dict) or len(step) != 1:
                raise StepError(f"{label}[{i}] は 1 つのアクションを持つ dict にしてください: {step}")
            (action, arg), = step.items()
            log.debug("step %s[%d] %s %s", label, i, action, self._mask(arg))
            try:
                self._do(action, arg, vars, f"{label}[{i}]")
            except StepError:
                raise
            except Exception as ex:
                shot = self._shot(f"error_{label}_{i}")
                raise StepError(f"{label}[{i}] {action} で失敗: {ex}（画面: {shot}）") from ex
        return vars

    def _mask(self, arg: Any) -> Any:
        s = str(arg)
        return "***" if any("{" + k in s for k in self.secret_keys) else arg

    def _shot(self, name: str) -> str:
        try:
            self.shot_dir.mkdir(parents=True, exist_ok=True)
            p = self.shot_dir / f"{name}.png"
            self.page.screenshot(path=str(p), full_page=True)
            return str(p)
        except Exception:
            return "(撮影失敗)"

    def _do(self, action: str, arg: Any, vars: dict[str, Any], where: str) -> None:
        page = self.page
        if action == "goto":
            page.goto(_fmt(arg, vars), wait_until="domcontentloaded")
        elif action == "click":
            page.locator(_fmt(arg, vars)).first.click()
        elif action == "check":
            page.locator(_fmt(arg, vars)).first.check()
        elif action == "fill":
            page.locator(_fmt(arg["selector"], vars)).first.fill(str(_fmt(arg["value"], vars)))
        elif action == "select":
            loc = page.locator(_fmt(arg["selector"], vars)).first
            value = str(_fmt(arg["value"], vars))
            try:
                loc.select_option(value=value)
            except Exception:
                loc.select_option(label=value)
        elif action == "press":
            page.locator(_fmt(arg["selector"], vars)).first.press(arg["key"])
        elif action == "wait_for":
            sel, timeout = (arg, 30) if isinstance(arg, str) else (arg["selector"], arg.get("timeout_sec", 30))
            page.locator(_fmt(sel, vars)).first.wait_for(timeout=float(timeout) * 1000)
        elif action == "wait_ms":
            page.wait_for_timeout(int(arg))
        elif action == "read_text":
            vars[arg["var"]] = page.locator(_fmt(arg["selector"], vars)).first.inner_text().strip()
        elif action == "expect_text":
            text = str(_fmt(arg, vars))
            if text not in page.locator("body").inner_text():
                shot = self._shot(f"expect_{where}")
                raise StepError(f"{where} 画面に「{text}」が見つかりません（画面: {shot}）")
        elif action == "screenshot":
            self._shot(_fmt(arg, vars))
        elif action == "pause":
            if arg:
                page.pause()
        elif action == "for_each_leg":
            for pos, n in enumerate(vars["selection"], start=1):
                self.run(arg, {**vars, "pos": pos, "n": n, "n2": f"{n:02d}"}, f"{where}.leg{pos}")
        elif action == "optional":
            try:
                self.run(arg, vars, f"{where}.optional")
            except StepError as ex:
                log.debug("optional step skipped: %s", ex)
        else:
            raise StepError(f"{where} 不明なアクション: {action}")
