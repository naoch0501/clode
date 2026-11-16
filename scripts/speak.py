"""学習したモデルで文章を読み上げて wav に保存する。

使い方:
  source scripts/env.sh
  python scripts/speak.py -m myvoice --text "こんにちは、今日はいい天気ですね。"
  python scripts/speak.py -m myvoice --file script.txt --out out.wav --speed 1.1
  python scripts/speak.py -m myvoice --list-styles

オプション:
  --style     スタイル名（既定: Neutral）。WebUI の「スタイル作成」で増やせる
  --speed     話速（1.0 が標準、大きいほど速い）
  --checkpoint 使うチェックポイント（省略時は最もステップ数の多いもの）
  --device    cuda / cpu（Radeon でも cuda と指定する）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SBV2_DIR = Path(os.environ.get("SBV2_DIR", REPO_ROOT / "Style-Bert-VITS2"))


def pick_checkpoint(model_dir: Path, name: str | None) -> Path:
    if name:
        p = model_dir / name
        if not p.exists():
            sys.exit(f"チェックポイントが見つかりません: {p}")
        return p
    ckpts = list(model_dir.glob("*.safetensors"))
    if not ckpts:
        sys.exit(f"{model_dir} に .safetensors がありません。学習が終わっているか確認してください。")

    def step(p: Path) -> int:
        m = re.search(r"_s(\d+)\.safetensors$", p.name)
        return int(m.group(1)) if m else -1

    return max(ckpts, key=lambda p: (step(p), p.stat().st_mtime))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-m", "--model", required=True, help="モデル名（model_assets/<名前>）")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--text", help="読み上げる文章")
    src.add_argument("--file", type=Path, help="読み上げる文章が書かれたテキストファイル")
    ap.add_argument("--out", type=Path, default=Path("output.wav"))
    ap.add_argument("--style", default="Neutral")
    ap.add_argument("--style-weight", type=float, default=1.0)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--checkpoint")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--list-styles", action="store_true", help="使えるスタイル名を表示して終了")
    args = ap.parse_args()

    model_dir = SBV2_DIR / "model_assets" / args.model
    config_path = model_dir / "config.json"
    if not config_path.exists():
        sys.exit(f"モデルが見つかりません: {model_dir}")

    styles = list(json.loads(config_path.read_text(encoding="utf-8"))["data"]["style2id"].keys())
    if args.list_styles:
        print("\n".join(styles))
        return
    if args.style not in styles:
        sys.exit(f"スタイル '{args.style}' はありません。使えるもの: {', '.join(styles)}")

    if args.file:
        text = args.file.read_text(encoding="utf-8").strip()
    elif args.text:
        text = args.text
    else:
        ap.error("--text か --file を指定してください")

    out_path = args.out.resolve()  # chdir 前に解決しておく
    ckpt = pick_checkpoint(model_dir, args.checkpoint)

    # Style-Bert-VITS2 本体は相対パスで bert/ や dict_data/ を参照するため移動してから import
    os.chdir(SBV2_DIR)
    sys.path.insert(0, str(SBV2_DIR))

    import torch
    from scipy.io import wavfile

    from style_bert_vits2.constants import Languages
    from style_bert_vits2.nlp import bert_models
    from style_bert_vits2.nlp.japanese import pyopenjtalk_worker
    from style_bert_vits2.nlp.japanese.user_dict import update_dict
    from style_bert_vits2.tts_model import TTSModel

    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        print("警告: GPU が使えないため CPU で実行します。", file=sys.stderr)
        device = "cpu"

    pyopenjtalk_worker.initialize_worker()
    update_dict()  # dict_data/ のユーザー辞書を反映
    bert_models.load_model(Languages.JP, device_map=device)
    bert_models.load_tokenizer(Languages.JP)

    print(f"モデル: {ckpt.name} / スタイル: {args.style} / デバイス: {device}")
    model = TTSModel(
        model_path=ckpt,
        config_path=config_path,
        style_vec_path=model_dir / "style_vectors.npy",
        device=device,
    )
    sr, audio = model.infer(
        text=text,
        language=Languages.JP,
        style=args.style,
        style_weight=args.style_weight,
        length=1.0 / args.speed,  # length は「長さ」の倍率なので話速の逆数
        line_split=True,  # 改行ごとに区切って合成
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(out_path, sr, audio)
    print(f"保存しました: {out_path} ({len(audio) / sr:.1f} 秒)")


if __name__ == "__main__":
    main()
