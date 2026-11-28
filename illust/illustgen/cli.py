from __future__ import annotations

import argparse
import os
import random
import re
import shlex
import sys
import time
from pathlib import Path

import yaml

from . import audit, duo, lora, safety
from .character import Character, ConfigFileError
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
    if char.cfg is not None:
        d["cfg"] = char.cfg
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
    style_spec = None if args.no_style else (args.style or char.style or None)
    if style_spec:
        sd = resolve_style_dir(style_spec)
        style = lora.Style.load(sd)
        lora.check_style(style)
        trig = style.trigger or lora.default_style_trigger(sd)
        fname = args.style_file or trig
        if not fname.endswith(".safetensors"):
            fname += ".safetensors"
        if args.style_strength is not None:
            strength = args.style_strength
        elif char.style_strength is not None and (not args.style or resolve_style_dir(args.style) == resolve_style_dir(char.style)):
            strength = char.style_strength
        else:
            strength = style.strength
        char.lora = [*char.lora, {"name": fname, "strength": strength, "trigger": trig}]
    if args.no_outfit:
        char.outfit = ""
    elif args.outfit is not None:
        safety.check_prompt(args.outfit, "outfit")
        char.outfit = args.outfit
    positive = join(d["quality_positive"], rating.get("positive", ""), safety.FORCED_POSITIVE,
                    char.prompt_fragment(), args.scene)
    if has_other_people(args.scene):
        positive = drop_solo(positive)
    negative = join(safety.FORCED_NEGATIVE, d["negative"], rating.get("negative", ""), args.negative or "")

    refs = char.reference_images[:1] if not args.no_reference else []
    wf_key = "with_reference" if refs else "without_reference"
    workflow = ROOT / cfg["workflows"][wf_key]

    char_out = ROOT / cfg.get("output_dir", "outputs") / Path(args.character).name
    out_dir = char_out / run_folder_name(args.name, args.scene, args.rating)
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
            path = out_dir / f"{seed + i}_{j}.png"
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
    write_run_note(out_dir, args, char, positive, negative, d, workflow, seed)
    print(f"\nフォルダ: {out_dir}")


OTHER_PEOPLE = re.compile(r"(?<![a-z])(\d+boys?|\d+girls|1other|hetero|yuri|multiple (boys|girls)|group sex|threesome)(?![a-z])", re.I)


def has_other_people(scene: str) -> bool:
    """シーンに男性や複数人の指定があるか（あれば character.yaml の solo を外す）。"""
    return bool(OTHER_PEOPLE.search(scene or ""))


def drop_solo(prompt: str) -> str:
    """「solo」（強さ付きも含む）だけを外す。「solo focus」は残す。"""
    tags = [t.strip() for t in prompt.split(",")]
    kept = [t for t in tags if re.sub(r"[()]|:[0-9.]+", "", t).strip().lower() != "solo"]
    return ", ".join(kept)


def run_folder_name(name: str | None, scene: str, rating: str) -> str:
    """生成 1 回ごとのフォルダ名: 日時_名前（--name がなければシーンの先頭のタグから作る）。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if name:
        label = name
    else:
        tags = [re.sub(r"[()]|:[0-9.]+", "", t).strip() for t in scene.split(",")]
        label = "_".join(t for t in tags if t)[:40]
        if rating == "r18":
            label = f"r18_{label}"
    label = re.sub(r'[\\/:*?"<>|\s]+', "-", label).strip("-.") or "run"
    return f"{stamp}_{label}"


def write_run_note(out_dir: Path, args, char, positive: str, negative: str, d: dict, workflow: Path,
                   seed: int) -> None:
    loras = ", ".join(f"{e['name']} ({e.get('strength', 0.8)})" for e in char.lora) or "なし"
    seeds = f"{seed}" if args.count == 1 else f"{seed} 〜 {seed + args.count - 1}"
    note = f"""キャラクター: {char.name}
レーティング: {args.rating}
シーン (-s): {args.scene}
seed: {seeds}
モデル: {d["checkpoint"]}
LoRA: {loras}
ワークフロー: {workflow.name}
サイズ: {d["width"]}x{d["height"]} / steps {d["steps"]} / cfg {d["cfg"]}

--- Positive ---
{positive}

--- Negative ---
{negative}

--- 同じ設定で 1 枚出し直すには（seed を変える） ---
python -m illustgen {" ".join(shlex.quote(a) for a in sys.argv[1:])}
"""
    (out_dir / "prompt.txt").write_text(note, encoding="utf-8")


def parse_lora_specs(specs: list[str]) -> list[dict]:
    out = []
    for spec in specs:
        name, _, strength = spec.partition(":")
        if not name.endswith(".safetensors"):
            name += ".safetensors"
        out.append({"name": name, "strength": float(strength or 0.8)})
    return out


def cmd_duo(args, cfg) -> None:
    """2 人を 1 枚に描く。構図 → 左の人（A の LoRA）→ 右の人（B の LoRA）の順に描く。"""
    chars = []
    for spec, outfit, extra, lora_spec in ((args.char_a, args.outfit_a, args.scene_a, args.lora_a),
                                           (args.char_b, args.outfit_b, args.scene_b, args.lora_b)):
        c = Character.load(spec)
        safety.check_character(c)
        if args.no_outfit:
            c.outfit = ""
        elif outfit is not None:
            safety.check_prompt(outfit, "outfit")
            c.outfit = outfit
        safety.check_prompt(extra or "", "scene")
        if lora_spec:
            c.lora = parse_lora_specs(lora_spec)
        chars.append((spec, c, extra or ""))
    safety.check_prompt(args.scene, "scene")
    (spec_a, a, extra_a), (spec_b, b, extra_b) = chars

    d = {**cfg["defaults"], "width": 1216, "height": 832}
    if a.cfg is not None:
        d["cfg"] = a.cfg
    for key in ("width", "height", "steps", "cfg", "checkpoint"):
        if getattr(args, key) is not None:
            d[key] = getattr(args, key)
    rating = cfg.get("ratings", {}).get(args.rating, {})

    style_loras, style_trig = [], ""
    style_spec = None if args.no_style else (args.style or a.style or None)
    if style_spec:
        sd = resolve_style_dir(style_spec)
        style = lora.Style.load(sd)
        lora.check_style(style)
        style_trig = style.trigger or lora.default_style_trigger(sd)
        if args.style_strength is not None:
            strength = args.style_strength
        elif a.style_strength is not None:
            strength = a.style_strength
        else:
            strength = style.strength
        style_loras = [{"name": f"{style_trig}.safetensors", "strength": strength}]

    def tags(c: Character) -> str:
        return drop_solo(join(c.appearance, c.outfit))

    def own_trigger(c: Character) -> str:
        return c.trigger if c.lora and c.trigger else ""

    head = join(d["quality_positive"], rating.get("positive", ""), safety.FORCED_POSITIVE, style_trig)
    base_prompt = join(head, "(2girls:1.3), side by side", args.scene)
    region_a = join("1girl", tags(a), extra_a)
    region_b = join("1girl", tags(b), extra_b)
    refine_a = join(head, own_trigger(a), "1girl", tags(a), args.scene, extra_a)
    refine_b = join(head, own_trigger(b), "1girl", tags(b), args.scene, extra_b)
    negative = join(safety.FORCED_NEGATIVE, d["negative"], rating.get("negative", ""), args.negative or "")

    pair = f"{Path(spec_a).name}_{Path(spec_b).name}"
    out_dir = ROOT / cfg.get("output_dir", "outputs") / "duo" / pair / run_folder_name(args.name, args.scene, args.rating)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / cfg.get("audit_log", "outputs/audit.jsonl")
    provider = make_provider(cfg)

    seed = args.seed if args.seed is not None else random.randint(0, 2**32 - 4)
    for i in range(args.count):
        s = seed + i * 3  # 構図・左・右で seed を 1 ずつ使うので 3 つずつずらす
        wf = duo.build(d, s, base_prompt, region_a, region_b, refine_a, refine_b, negative,
                       style_loras, a.lora, b.lora, denoise=args.refine)
        print(f"[{i + 1}/{args.count}] seed={s} を生成中（構図 → 左: {a.name} → 右: {b.name}）...", flush=True)
        images = provider.run(wf)
        saved = []
        for j, data in enumerate(images):
            path = out_dir / f"{s}_{j}.png"
            path.write_bytes(data)
            saved.append(path)
            print(f"  保存: {path}")
        audit.write(log_path, {
            "mode": "duo", "characters": [a.name, b.name], "ages": [a.age, b.age], "rating": args.rating,
            "positive": {"base": base_prompt, "left": refine_a, "right": refine_b}, "negative": negative,
            "seed": s, "checkpoint": d["checkpoint"], "loras": {"style": style_loras, "left": a.lora, "right": b.lora},
            "outputs": [str(p) for p in saved],
        })
    note = f"""2 人（duo）
左: {a.name} / LoRA: {", ".join(f"{e['name']} ({e.get('strength', 0.8)})" for e in a.lora) or "なし"}
右: {b.name} / LoRA: {", ".join(f"{e['name']} ({e.get('strength', 0.8)})" for e in b.lora) or "なし"}
画風: {", ".join(f"{e['name']} ({e['strength']})" for e in style_loras) or "なし"}
レーティング: {args.rating}
seed: {seed}（1 枚ごとに 3 ずつ増える）
サイズ: {d["width"]}x{d["height"]} / steps {d["steps"]} / cfg {d["cfg"]} / 描き直しの強さ {args.refine}

--- 構図: 全体 ---
{base_prompt}

--- 構図: 左の人 ---
{region_a}

--- 構図: 右の人 ---
{region_b}

--- 描き直し: 左の人 ---
{refine_a}

--- 描き直し: 右の人 ---
{refine_b}

--- Negative ---
{negative}

--- 同じ設定でもう一度 ---
python -m illustgen {" ".join(shlex.quote(x) for x in sys.argv[1:])}
"""
    (out_dir / "prompt.txt").write_text(note, encoding="utf-8")
    print(f"\nフォルダ: {out_dir}")


def resolve_style_dir(spec: str) -> Path:
    p = Path(spec)
    if not p.exists() and (ROOT / "styles" / spec).exists():
        p = ROOT / "styles" / spec
    return p.resolve()


def lora_target(args):
    """(フォルダ, トリガーワード, 表示名, 再実行用の引数, 画風かどうか)"""
    if args.style:
        d = resolve_style_dir(args.style)
        style = lora.Style.load(d)
        lora.check_style(style)
        return d, style.trigger or lora.default_style_trigger(d), f"--style {args.style}", True
    d = Path(args.character).resolve()
    char = Character.load(d)
    safety.check_character(char)
    return d, char.trigger or lora.default_trigger(d), f"-c {args.character}", False


def cmd_lora_prep(args, cfg) -> None:
    d, trigger, again, is_style = lora_target(args)
    kind = "画風" if is_style else "キャラクター"
    print(f"==> {kind} LoRA の学習データを準備: トリガーワード「{trigger}」")
    if is_style:
        out = lora.prepare_style(d, lora.Style.load(d), trigger)
    else:
        out = lora.prepare(d, Character.load(d), trigger)
    n = len(list(out.glob("*.png")))
    print(f"\n完了: {n} 枚 → {out}")
    print("  .txt を開いてタグを確認できます（間違ったタグは消してよい）。")
    yaml_name = "style.yaml" if is_style else "character.yaml"
    if (is_style and not lora.Style.load(d).trigger) or (not is_style and not Character.load(d).trigger):
        print(f"  {yaml_name} に次の 1 行を追加してください:  trigger: {trigger}")
    print(f"  次:  python -m illustgen lora-train {again}")


def free_comfyui(cfg) -> None:
    try:
        provider = make_provider(cfg)
        provider.session.post(f"{provider.url}/free", json={"unload_models": True, "free_memory": True}, timeout=10)
        print("  ComfyUI のモデルを VRAM から外しました（学習中は ComfyUI で生成しないでください）。")
    except Exception:  # noqa: BLE001
        pass


def cmd_lora_train(args, cfg) -> None:
    char_dir, trigger, again, is_style = lora_target(args)
    dataset = char_dir / "lora" / "dataset"
    images = list(dataset.glob("*.png")) if dataset.is_dir() else []
    if not images:
        sys.exit(f"学習データがありません。先に  python -m illustgen lora-prep {again}")

    repo_root = ROOT.parent
    comfy_models = repo_root / "ComfyUI" / "models"
    base = Path(args.base) if args.base else comfy_models / "checkpoints" / cfg["defaults"]["checkpoint"]
    if not base.exists():
        sys.exit(f"学習の元にするモデルがありません: {base}")
    repeats = args.repeats or lora.auto_repeats(len(images), args.epochs, 2000 if is_style else 1600)
    toml_path = char_dir / "lora" / "dataset.toml"
    toml_path.write_text(lora.dataset_toml(dataset, repeats), encoding="utf-8")
    out_dir = comfy_models / "loras"
    cmd = lora.train_command(repo_root, base, toml_path, out_dir, trigger, args.epochs, args.dim, args.lr,
                             char_dir / "lora" / "logs")
    steps = len(images) * repeats * args.epochs
    print(f"==> LoRA 学習: {len(images)} 枚 × {repeats} 回 × {args.epochs} エポック = {steps} ステップ")
    print(f"    元モデル: {base.name} / 出力: {out_dir}/{trigger}.safetensors")
    if args.dry_run:
        print(" ".join(cmd))
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    free_comfyui(cfg)
    code = lora.run_training(repo_root, cmd)
    if code != 0:
        sys.exit(f"学習が失敗しました (終了コード {code})。上のエラーを確認してください。")
    if is_style:
        print(f"""
完了。2 エポックごとの途中経過も {out_dir}/ に保存されています（{trigger}-000002.safetensors など）。
style.yaml に trigger: {trigger} があることを確認して、次のように使います:

  python -m illustgen generate -c characters/meto -s "..." --no-reference --style {args.style}
  # 途中経過と比べる
  python -m illustgen generate -c characters/meto -s "..." --no-reference --style {args.style} --style-file {trigger}-000006
""")
        return
    print(f"""
完了。2 エポックごとの途中経過も {out_dir}/ に保存されています（{trigger}-000002.safetensors など）。
character.yaml に次を書くと、generate で自動的に使われます:

trigger: {trigger}
lora:
  - name: {trigger}.safetensors
    strength: 0.8

途中経過と比べるには:  python -m illustgen generate {again} -s "..." --no-reference --lora {trigger}-000006
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
    p.add_argument("--name", help="保存フォルダの名前（省略時はシーンの先頭のタグから作る）")
    p.add_argument("--seed", type=int)
    p.add_argument("--negative", help="追加のネガティブプロンプト")
    p.add_argument("--weight", type=float, help="参考画像の効き具合 (0.0-1.0)")
    p.add_argument("--start-at", dest="reference_start_at", type=float,
                   help="参考画像を効かせ始めるタイミング 0.0-1.0（上げると色の濁りが減る）")
    p.add_argument("--end-at", dest="reference_end_at", type=float, help="参考画像を効かせ終わるタイミング 0.0-1.0")
    p.add_argument("--style", help="画風 LoRA を重ねる（styles/<名前> または フォルダのパス）")
    p.add_argument("--no-style", action="store_true", help="character.yaml の style を使わない（画風なしで描く）")
    p.add_argument("--style-strength", type=float, help="画風 LoRA の強さ（省略時は style.yaml の strength）")
    p.add_argument("--style-file", help="画風 LoRA のファイル名（途中経過と比べるとき。例: mystyle_style-000006）")
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

    p = sub.add_parser("duo", help="2 人を 1 枚に描く（左: -a、右: -b）")
    p.add_argument("-a", "--char-a", required=True, help="左の人: characters/<名前>")
    p.add_argument("-b", "--char-b", required=True, help="右の人: characters/<名前>")
    p.add_argument("-s", "--scene", required=True, help="2 人に共通のシーン・構図のタグ")
    p.add_argument("--scene-a", help="左の人だけのタグ（ポーズ・表情など）")
    p.add_argument("--scene-b", help="右の人だけのタグ")
    p.add_argument("--outfit-a", help="左の人の服装（character.yaml の outfit の代わり）")
    p.add_argument("--outfit-b", help="右の人の服装")
    p.add_argument("--no-outfit", action="store_true", help="2 人とも服装タグを入れない")
    p.add_argument("--lora-a", action="append", metavar="名前[:強さ]", help="左の人の LoRA（character.yaml の代わり）")
    p.add_argument("--lora-b", action="append", metavar="名前[:強さ]", help="右の人の LoRA")
    p.add_argument("--refine", type=float, default=0.55,
                   help="それぞれの LoRA で描き直す強さ 0.3-0.8（上げるとキャラに似るが、構図や境目が崩れやすい）")
    p.add_argument("-r", "--rating", choices=["general", "r18"], default="general")
    p.add_argument("-n", "--count", type=int, default=1)
    p.add_argument("--name", help="保存フォルダの名前")
    p.add_argument("--seed", type=int)
    p.add_argument("--negative", help="追加のネガティブプロンプト")
    p.add_argument("--style", help="画風（省略時は -a の character.yaml の style）")
    p.add_argument("--style-strength", type=float)
    p.add_argument("--no-style", action="store_true")
    p.add_argument("--width", type=int, help="既定 1216（横長）")
    p.add_argument("--height", type=int, help="既定 832")
    p.add_argument("--steps", type=int)
    p.add_argument("--cfg", type=float)
    p.add_argument("--checkpoint")
    p.set_defaults(func=cmd_duo)

    p = sub.add_parser("lora-prep", help="LoRA 学習データを準備する（train/ の画像に自動でタグ付け）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("-c", "--character", help="キャラクター LoRA: characters/<名前>")
    g.add_argument("--style", help="画風 LoRA: styles/<名前>")
    p.set_defaults(func=cmd_lora_prep)

    p = sub.add_parser("lora-train", help="キャラクター / 画風 LoRA を学習する")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("-c", "--character", help="キャラクター LoRA: characters/<名前>")
    g.add_argument("--style", help="画風 LoRA: styles/<名前>")
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
    except ConfigFileError as e:
        sys.exit(f"[設定ファイルの誤り] {e}")
    except FileNotFoundError as e:
        sys.exit(f"[ファイルが見つかりません] {e.filename}\n  -c のフォルダ名が合っているか確認してください（例: characters/meto）。")


if __name__ == "__main__":
    main()
