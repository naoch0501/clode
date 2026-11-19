from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


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

    @classmethod
    def load(cls, path: str | Path) -> "Character":
        path = Path(path)
        if path.is_dir():
            path = path / "character.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
        )

    def prompt_fragment(self) -> str:
        return ", ".join(p for p in (self.appearance, self.outfit) if p)
