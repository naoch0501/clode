from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigFileError(Exception):
    pass


def load_yaml(path: Path) -> dict:
    """YAML を読む。書き方の誤りは、行番号つきの日本語のメッセージにする。"""
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = ""
        if mark is not None:
            lines = text.splitlines()
            start = max(0, mark.line - 1)  # YAML は誤りの次の行で気づくことがあるので、直前の行も出す
            for n in range(start, min(mark.line + 1, len(lines))):
                where += f"\n  {n + 1} 行目: {lines[n][:120]}"
            where += "\n  （このあたりを確認してください）"
        raise ConfigFileError(
            f"{path} の書き方に誤りがあります。{where}\n"
            "よくある原因:\n"
            "  ・「項目名:」のコロンのあとに半角スペースがない（例: reference_images:[] → reference_images: []）\n"
            "  ・2つの行が1行につながっている（\"…\"outfit: のように、閉じた \" のすぐ後ろに次の項目がある）\n"
            "  ・lora: の下の行の字下げ（先頭の半角スペース 2 つ）がずれている\n"
            "  ・\" で始めた値を \" で閉じていない"
        ) from None
    if not isinstance(data, dict):
        raise ConfigFileError(f"{path} の書き方に誤りがあります（「項目名: 値」の形になっていません）。")
    return data


@dataclass
class Character:
    name: str
    age: int | None
    declared_original: bool
    description: str = ""
    appearance: str = ""
    outfit: str = ""
    lora: list[dict] = field(default_factory=list)
    reference_images: list[Path] = field(default_factory=list)
    reference_weight: float = 0.7
    trigger: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "Character":
        path = Path(path)
        if path.is_dir():
            path = path / "character.yaml"
        data = load_yaml(path)
        base = path.parent
        refs = [(base / p).resolve() for p in data.get("reference_images", [])]
        age = data.get("age")
        return cls(
            name=str(data.get("name", base.name)),
            age=age if isinstance(age, int) else None,
            declared_original=data.get("source") == "original",
            description=data.get("description", ""),
            appearance=data.get("appearance", ""),
            outfit=data.get("outfit", ""),
            lora=data.get("lora", []) or [],
            reference_images=refs,
            reference_weight=float(data.get("reference_weight", 0.7)),
            trigger=str(data.get("trigger") or ""),
        )

    def prompt_fragment(self) -> str:
        own = self.trigger and any(not e.get("trigger") for e in self.lora)
        triggers = [self.trigger] if own else []
        triggers += [str(e["trigger"]) for e in self.lora if e.get("trigger")]
        return ", ".join(p for p in (*triggers, self.appearance, self.outfit) if p)
