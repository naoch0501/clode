from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

import yaml

from . import audit, safety
from .character import Character
from .providers import PROVIDERS

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        sys.exit(f"設定ファイルがありません: {p}\n  cp config.example.yaml config.yaml で作成してください。")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def make_provider(cfg: dict):
    name = cfg.get("provider", "comfyui")
    opts = dict(cfg.get(name, {}))
    env = opts.pop("api_key_env", None)
    if env and os.environ.get(env):
        opts["api_key"] = os.environ[env]
    return PROVIDERS[name](**opts)


def join(*parts: str) -> str:
    return ", ".join(p.strip(" ,") for p in parts if p and p.strip(" ,"))


def validate(char: Character, scene: str, extra_negative: str) -> None:
    safety.check_character(char)
    safety.check_prompt(scene, "scene")
    # ネガティブ側は未成年語が入ってよい（除外のため）ので検査しない
    for ref in char.reference_images:
        if not ref.exists():
            raise safety.SafetyError(f"参考画像が見つかりません: {ref}")
        safety.check_reference_image(ref)


def cmd_check(args, cfg) -> None:
    char = Character.load(args.character)
    validate(char, args.scene or "", "")
    print(f"OK: {char.name} (age {char.age}, 参考画像 {len(char.reference_images)} 枚)")


def cmd_ping(args, cfg) -> None:
    stats = make_provider(cfg).ping()
    for dev in stats.get("devices", []):
        print(f"GPU: {dev.get('name')}  VRAM: {dev.get('vram_total', 0) / 2**30:.1f} GB")
    print("接続 OK")


def cmd_generate(args, cfg) -> None:
    char = Character.load(args.character)
    validate(char, args.scene, args.negative or "")

    d = {**cfg["defaults"]}
    for key in ("width", "height", "steps", "cfg", "checkpoint"):
        if getattr(args, key) is not None:
            d[key] = getattr(args, key)
    rating = cfg.get("ratings", {}).get(args.rating, {})

    positive = join(d["quality_positive"], rating.get("positive", ""), safety.FORCED_POSITIVE,
                    char.prompt_fragment(), args.scene)
    negative = join(safety.FORCED_NEGATIVE, d["negative"], rating.get("negative", ""), args.negative or "")

    refs = char.reference_images[:1] if not args.no_reference else []
    wf_key = "with_reference" if refs else "without_reference"
    workflow = ROOT / cfg["workflows"][wf_key]

    out_dir = ROOT / cfg.get("output_dir", "outputs") / Path(args.character).name
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / cfg.get("audit_log", "outputs/audit.jsonl")
    provider = make_provider(cfg)

    seed = args.seed if args.seed is not None else random.randint(0, 2**32 - 1)
    for i in range(args.count):
        values = {
            **d, "positive": positive, "negative": negative, "seed": seed + i,
            "batch_size": 1, "reference_weight": args.weight if args.weight is not None else char.reference_weight,
        }
        print(f"[{i + 1}/{args.count}] seed={seed + i} を生成中...", flush=True)
        images = provider.generate(workflow, values, refs, char.lora)
        saved = []
        for j, data in enumerate(images):
            path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}_{seed + i}_{j}.png"
            path.write_bytes(data)
            saved.append(path)
            print(f"  保存: {path}")
        audit.write(log_path, {
            "character": char.name, "age": char.age, "rating": args.rating,
            "positive": positive, "negative": negative, "seed": seed + i,
            "workflow": workflow.name, "checkpoint": d["checkpoint"], "loras": char.lora,
            "references": [{"file": str(r), "sha256": audit.sha256(r)} for r in refs],
            "outputs": [str(p) for p in saved],
        })


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="illustgen", description="キャラクターデザインから成人キャラのイラストを生成")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ping", help="ComfyUI への接続確認")
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("check", help="キャラクター定義と参考画像の安全チェックのみ行う")
    p.add_argument("-c", "--character", required=True)
    p.add_argument("--scene")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("generate", help="生成する")
    p.add_argument("-c", "--character", required=True, help="characters/<名前> フォルダ")
    p.add_argument("-s", "--scene", required=True, help="シーン・ポーズ・構図のタグ")
    p.add_argument("-r", "--rating", choices=["general", "r18"], default="general")
    p.add_argument("-n", "--count", type=int, default=1)
    p.add_argument("--seed", type=int)
    p.add_argument("--negative", help="追加のネガティブプロンプト")
    p.add_argument("--weight", type=float, help="参考画像の効き具合 (0.0-1.0)")
    p.add_argument("--no-reference", action="store_true", help="参考画像を使わない（LoRA のみ）")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--steps", type=int)
    p.add_argument("--cfg", type=float)
    p.add_argument("--checkpoint")
    p.set_defaults(func=cmd_generate)

    args = ap.parse_args(argv)
    cfg = load_config(args.config) if args.cmd != "check" or Path(args.config).exists() else {}
    try:
        args.func(args, cfg)
    except safety.SafetyError as e:
        sys.exit(f"[安全チェックで停止] {e}")


if __name__ == "__main__":
    main()
