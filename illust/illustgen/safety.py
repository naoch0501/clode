"""安全チェック。

このツールは「自作の成人キャラクター」専用です。次の2点を機械的に防ぎます。
  1. 未成年（または未成年に見える）表現
  2. 実在人物の写真を素材にすること（ディープフェイク防止）
チェックは完全ではありません。最終的な責任は利用者にあります。
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

MIN_AGE = 18

# 未成年を示す・連想させる語。部分一致で判定する（英語は単語境界）。
MINOR_TERMS_EN = [
    "loli", "lolita", "shota", "shotacon", "child", "children", "kid", "kids",
    "toddler", "infant", "minor", "underage", "preteen", "teen", "teenage",
    "teenager", "young girl", "young boy", "little girl", "little boy",
    "schoolgirl", "schoolboy", "elementary school", "middle school", "high school",
    "jk", "jc", "js", "kindergarten", "randoseru", "flat chest", "petite body",
    "child body", "aged down", "age regression",
]
MINOR_TERMS_JA = [
    "ロリ", "ショタ", "幼女", "幼児", "幼い", "子供", "子ども", "こども", "児童",
    "少女", "少年", "小学", "中学", "高校", "女子高生", "男子高生", "女子中学生",
    "未成年", "ランドセル", "園児", "JK", "ＪＫ", "幼児体型", "つるぺた",
]
# 写実・実在人物を示す語（イラストに限定し、実在人物の性的画像化を防ぐ）
REAL_PERSON_TERMS_EN = [
    "photo", "photograph", "photorealistic", "realistic", "real person",
    "celebrity", "actress", "actor", "idol", "raw photo", "dslr", "8k uhd",
    "instagram", "selfie",
]
REAL_PERSON_TERMS_JA = ["実写", "写真", "実在", "芸能人", "女優", "俳優", "アイドル", "自撮り"]

# 必ずネガティブプロンプトに入れる語
FORCED_NEGATIVE = (
    "child, loli, shota, minor, underage, teen, young, aged down, flat chest, "
    "school uniform, photo, photorealistic, realistic, real person"
)
# 必ずポジティブプロンプトに入れる語
FORCED_POSITIVE = "adult, mature, illustration, anime style"

# 写真らしさを示す EXIF タグ (Make, Model, ExposureTime, FNumber, ISOSpeedRatings, LensModel)
PHOTO_EXIF_TAGS = {0x010F, 0x0110, 0x829A, 0x829D, 0x8827, 0xA434}


class SafetyError(Exception):
    pass


def _find_terms(text: str) -> list[str]:
    hits = []
    lower = text.lower()
    for term in MINOR_TERMS_EN + REAL_PERSON_TERMS_EN:
        if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", lower):
            hits.append(term)
    for term in MINOR_TERMS_JA + REAL_PERSON_TERMS_JA:
        if term.lower() in lower:
            hits.append(term)
    return hits


def check_prompt(text: str, field: str = "prompt") -> None:
    hits = _find_terms(text)
    if hits:
        raise SafetyError(
            f"{field} に使用できない語が含まれています: {', '.join(sorted(set(hits)))}\n"
            "このツールは成人の架空キャラクターのイラスト専用です。"
        )


def check_character(char) -> None:
    if not char.declared_original:
        raise SafetyError(
            f"キャラクター '{char.name}': source: original と宣言されていません。"
            "自作（または権利を持つ）架空キャラクターのみ使用できます。"
        )
    if char.age is None or char.age < MIN_AGE:
        raise SafetyError(
            f"キャラクター '{char.name}': age は {MIN_AGE} 以上の数値を指定してください（現在: {char.age}）。"
        )
    for field in ("name", "description", "hair", "appearance", "outfit", "trigger"):
        check_prompt(str(getattr(char, field) or ""), f"キャラクター '{char.name}' の {field}")
    for name, tags in (getattr(char, "hairstyles", None) or {}).items():
        check_prompt(tags, f"キャラクター '{char.name}' の髪型 {name}")


def check_reference_image(path: Path) -> None:
    """カメラで撮影された写真らしい画像を拒否する（EXIF による簡易判定）。"""
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            exif = img.getexif()
            tags = set(exif.keys())
            try:
                tags |= set(exif.get_ifd(0x8769).keys())  # Exif IFD
            except Exception:
                pass
    except Exception as e:  # noqa: BLE001
        raise SafetyError(f"参考画像を読み込めません: {path} ({e})") from e
    found = tags & PHOTO_EXIF_TAGS
    if found:
        raise SafetyError(
            f"参考画像 {path.name} にカメラの撮影情報 (EXIF) があります。"
            "実在人物の写真は使用できません。キャラクターデザイン画を使ってください。"
        )
