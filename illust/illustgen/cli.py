from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

import yaml

from . import audit, lora, safety
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

    d = {"reference_start_at": 0.0, "reference_end_at": 0.8, **cfg["defaults"]}
    for key in ("width", "height", "steps", "cfg", "checkpoint", "reference_start_at", "reference_end_at"):
        if getattr(args, key) is not None:
            d[key] = getattr(args, key)
    rating = cfg.get("ratings", {}).get(args.rating, {})

    if args.lora:
        char.lora = []
        for spec in args.lora:
            name, _, strength = spec.partition(":")
            if not name.endswith(".safetensors"):
                name += ".safetensors"
            char.lora.append({"name": name, "strength": float(strength or 0.8)})
    if args.no_outfit:
        char.outfit = ""
    elif args.outfit is not None:
        safety.check_prompt(args.outfit, "outfit")
        char.outfit = args.outfit
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


def cmd_lora_prep(args, cfg) -> None:
    char_dir = Path(args.character).resolve()
    char = Character.load(char_dir)
    trigger = char.trigger or lora.default_trigger(char_dir)
    print(f"==> 学習データを準備: トリガーワード「{trigger}」")
    out = lora.prepare(char_dir, char, trigger)
    n = len(list(out.glob("*.png")))
    print(f"\n完了: {n} 枚 → {out}")
    print("  .txt を開いてタグを確認できます（間違ったタグは消してよい）。")
    if not char.trigger:
        print(f"  character.yaml に次の 1 行を追加してください:  trigger: {trigger}")
    print(f"  次:  python -m illustgen lora-train -c {args.character}")


def free_comfyui(cfg) -> None:
    try:
        provider = make_provider(cfg)
        provider.session.post(f"{provider.url}/free", json={"unload_models": True, "free_memory": True}, timeout=10)
        print("  ComfyUI のモデルを VRAM から外しました（学習中は ComfyUI で生成しないでください）。")
    except Exception:  # noqa: BLE001
        pass


def cmd_lora_train(args, cfg) -> None:
    char_dir = Path(args.character).resolve()
    char = Character.load(char_dir)
    safety.check_character(char)
    trigger = char.trigger or lora.default_trigger(char_dir)
    dataset = char_dir / "lora" / "dataset"
    images = list(dataset.glob("*.png")) if dataset.is_dir() else []
    if not images:
        sys.exit(f"学習データがありません。先に  python -m illustgen lora-prep -c {args.character}")

    repo_root = ROOT.parent
    comfy_models = repo_root / "ComfyUI" / "models"
    base = Path(args.base) if args.base else comfy_models / "checkpoints" / cfg["defaults"]["checkpoint"]
    if not base.exists():
        sys.exit(f"学習の元にするモデルがありません: {base}")
    repeats = args.repeats or lora.auto_repeats(len(images), args.epochs)
    toml_path = char_dir / "lora" / "dataset.toml"
    toml_path.write_text(lora.dataset_toml(dataset, repeats), encoding="utf-8")
    out_dir = comfy_models / "loras"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = lora.train_command(repo_root, base, toml_path, out_dir, trigger, args.epochs, args.dim, args.lr,
                             char_dir / "lora" / "logs")
    steps = len(images) * repeats * args.epochs
    print(f"==> LoRA 学習: {len(images)} 枚 × {repeats} 回 × {args.epochs} エポック = {steps} ステップ")
    print(f"    元モデル: {base.name} / 出力: {out_dir}/{trigger}.safetensors")
    if args.dry_run:
        print(" ".join(cmd))
        return
    free_comfyui(cfg)
    code = lora.run_training(repo_root, cmd)
    if code != 0:
        sys.exit(f"学習が失敗しました (終了コード {code})。上のエラーを確認してください。")
    print(f"""
完了。2 エポックごとの途中経過も {out_dir}/ に保存されています（{trigger}-000002.safetensors など）。
character.yaml に次を書くと、generate で自動的に使われます:

trigger: {trigger}
lora:
  - name: {trigger}.safetensors
    strength: 0.8

途中経過と比べるには:  python -m illustgen generate -c {args.character} -s "..." --no-reference --lora {trigger}-000006
""")


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
    p.add_argument("--start-at", dest="reference_start_at", type=float,
                   help="参考画像を効かせ始めるタイミング 0.0-1.0（上げると色の濁りが減る）")
    p.add_argument("--end-at", dest="reference_end_at", type=float, help="参考画像を効かせ終わるタイミング 0.0-1.0")
    p.add_argument("--outfit", help="この回だけ服装タグを差し替える（character.yaml の outfit の代わり）")
    p.add_argument("--no-outfit", action="store_true", help="この回は服装タグを入れない")
    p.add_argument("--lora", action="append", metavar="名前[:強さ]",
                   help="この回だけ使う LoRA（複数可）。例: --lora meto_oc-000006:0.9")
    p.add_argument("--no-reference", action="store_true", help="参考画像を使わない（LoRA のみ）")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--steps", type=int)
    p.add_argument("--cfg", type=float)
    p.add_argument("--checkpoint")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("lora-prep", help="LoRA 学習データを準備する（train/ の画像に自動でタグ付け）")
    p.add_argument("-c", "--character", required=True)
    p.set_defaults(func=cmd_lora_prep)

    p = sub.add_parser("lora-train", help="キャラクター LoRA を学習する")
    p.add_argument("-c", "--character", required=True)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--repeats", type=int, help="1 エポックで各画像を何回使うか（省略時は自動）")
    p.add_argument("--dim", type=int, default=16, help="LoRA の大きさ (network_dim)")
    p.add_argument("--lr", type=float, default=1e-4, help="学習率")
    p.add_argument("--base", help="学習の元にするモデルのパス（省略時は config の checkpoint）")
    p.add_argument("--dry-run", action="store_true", help="実行するコマンドを表示するだけ")
    p.set_defaults(func=cmd_lora_train)

    args = ap.parse_args(argv)
    cfg = load_config(args.config) if args.cmd not in ("check", "lora-prep") or Path(args.config).exists() else {}
    try:
        args.func(args, cfg)
    except safety.SafetyError as e:
        sys.exit(f"[安全チェックで停止] {e}")


if __name__ == "__main__":
    main()
