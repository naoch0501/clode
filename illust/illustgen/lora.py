"""キャラクター LoRA の学習データ準備と学習（kohya-ss/sd-scripts）。

  characters/<名前>/train/        ← 学習に使う画像を置く（15〜30 枚目安）
  characters/<名前>/lora/dataset/ ← lora-prep が作る（画像 + .txt キャプション）
  ComfyUI/models/loras/<trigger>.safetensors ← lora-train の出力
"""
from __future__ import annotations

import csv
import re
import shlex
import subprocess
import sys
from pathlib import Path

from PIL import Image

from . import safety
from .character import Character

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
TAGGER_REPO = "SmilingWolf/wd-vit-tagger-v3"
GENERAL_THRESHOLD = 0.35
KEEP_UNDERSCORE = {"^_^", ">_<", "o_o", "0_0", "u_u", "x_x", "=_=", ";_;", "+_+", "._.", "-_-", "<o>_<o>"}
# キャラ固有の特徴（トリガーワードに覚えさせるため、キャプションから外す）
INTRINSIC_PATTERNS = [
    r".* hair$", r".* eyes$", r"^hair .*", r".*bangs$", r"^sidelocks$", r"^ahoge$",
    r".* pupils$", r"^eyelashes$", r"^gradient eyes$",
]
GENERIC_KEEP = {"1girl", "1boy", "solo", "adult woman", "adult man", "mature female", "mature male"}


def default_trigger(char_dir: Path) -> str:
    base = re.sub(r"[^a-z0-9]", "", char_dir.name.lower()) or "char"
    return f"{base}_oc"


# --- 画像 ----------------------------------------------------------------------
def to_rgb_white(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        out = Image.new("RGB", rgba.size, (255, 255, 255))
        out.paste(rgba, mask=rgba.split()[3])
        return out
    return img.convert("RGB")


def list_images(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXTS)


# --- タグ付け ----------------------------------------------------------------------
class WDTagger:
    """WD14 tagger (ONNX)。初回に Hugging Face からモデル (約 400MB) を取得する。"""

    def __init__(self, repo: str = TAGGER_REPO):
        try:
            import numpy as np
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
        except ImportError:
            sys.exit("自動タグ付けの部品がありません:  uv pip install -r requirements-lora.txt")
        self.np = np
        model = hf_hub_download(repo, "model.onnx")
        tags_csv = hf_hub_download(repo, "selected_tags.csv")
        self.session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.size = int(inp.shape[1])
        with open(tags_csv, newline="", encoding="utf-8") as f:
            self.tags = [(row["name"], int(row["category"])) for row in csv.DictReader(f)]

    def __call__(self, img: Image.Image) -> list[str]:
        np = self.np
        img = to_rgb_white(img)
        side = max(img.size)
        square = Image.new("RGB", (side, side), (255, 255, 255))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        square = square.resize((self.size, self.size), Image.BICUBIC)
        arr = np.asarray(square, dtype=np.float32)[:, :, ::-1]  # RGB -> BGR
        probs = self.session.run(None, {self.input_name: arr[None, ...]})[0][0]
        found = [(name, p) for (name, cat), p in zip(self.tags, probs) if cat == 0 and p >= GENERAL_THRESHOLD]
        found.sort(key=lambda x: -x[1])
        return [n if n in KEEP_UNDERSCORE else n.replace("_", " ") for n, _ in found]


def build_caption(trigger: str, tags: list[str], char: Character) -> str:
    own = {t.strip().lower() for t in (char.appearance or "").split(",") if t.strip()} - GENERIC_KEEP
    kept = []
    for t in tags:
        tl = t.lower()
        if tl in own or any(re.fullmatch(p, tl) for p in INTRINSIC_PATTERNS):
            continue
        kept.append(t)
    return ", ".join([trigger, *kept])


def prepare(char_dir: Path, char: Character, trigger: str, tagger=None) -> Path:
    safety.check_character(char)
    src = char_dir / "train"
    if not src.is_dir() or not list_images(src):
        raise safety.SafetyError(f"{src}/ に学習用の画像を置いてください（15〜30 枚が目安）。")
    images = list_images(src)
    for p in images:
        safety.check_reference_image(p)

    tagger = tagger or WDTagger()
    out = char_dir / "lora" / "dataset"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():
        old.unlink()

    problems = []
    for i, p in enumerate(images, 1):
        with Image.open(p) as img:
            rgb = to_rgb_white(img)
        tags = tagger(rgb)
        hits = safety._find_terms(", ".join(tags))
        if hits:
            problems.append(f"{p.name}: {', '.join(sorted(set(hits)))}")
            continue
        stem = f"{i:03d}"
        rgb.save(out / f"{stem}.png")
        (out / f"{stem}.txt").write_text(build_caption(trigger, tags, char), encoding="utf-8")
        print(f"  {p.name} → {stem}.txt: {(out / f'{stem}.txt').read_text(encoding='utf-8')[:120]}")
    if problems:
        for f in out.iterdir():
            f.unlink()
        raise safety.SafetyError(
            "次の画像は、未成年または実写と判定されるタグが付いたため使えません。取り除いてやり直してください:\n  "
            + "\n  ".join(problems)
        )
    return out


# --- 学習 ----------------------------------------------------------------------
def dataset_toml(image_dir: Path, repeats: int, resolution: int = 1024) -> str:
    return f'''[general]
caption_extension = ".txt"
shuffle_caption = false
keep_tokens = 1

[[datasets]]
resolution = {resolution}
batch_size = 1
enable_bucket = true
min_bucket_reso = 512
max_bucket_reso = 1536
bucket_reso_steps = 64

  [[datasets.subsets]]
  image_dir = "{image_dir.as_posix()}"
  num_repeats = {repeats}
'''


def auto_repeats(n_images: int, epochs: int, target_steps: int = 1600) -> int:
    return max(1, round(target_steps / max(1, n_images * epochs)))


def train_command(repo_root: Path, base_model: Path, toml_path: Path, out_dir: Path, name: str,
                  epochs: int, dim: int, lr: float, log_dir: Path) -> list[str]:
    venv = repo_root / "venv-train"
    script = repo_root / "sd-scripts" / "sdxl_train_network.py"
    return [
        str(venv / "bin" / "accelerate"), "launch",
        "--num_processes", "1", "--num_machines", "1", "--mixed_precision", "bf16",
        "--dynamo_backend", "no", "--num_cpu_threads_per_process", "1",
        str(script),
        "--pretrained_model_name_or_path", str(base_model),
        "--dataset_config", str(toml_path),
        "--output_dir", str(out_dir), "--output_name", name, "--save_model_as", "safetensors",
        "--network_module", "networks.lora", "--network_dim", str(dim), "--network_alpha", str(max(1, dim // 2)),
        "--network_train_unet_only",
        "--learning_rate", str(lr), "--unet_lr", str(lr),
        "--optimizer_type", "AdamW", "--lr_scheduler", "cosine",
        "--max_train_epochs", str(epochs), "--save_every_n_epochs", "2",
        "--mixed_precision", "bf16", "--save_precision", "fp16",
        "--gradient_checkpointing", "--sdpa",
        "--cache_latents", "--cache_latents_to_disk", "--cache_text_encoder_outputs",
        "--no_half_vae", "--min_snr_gamma", "5",
        "--seed", "42", "--max_data_loader_n_workers", "0",
        "--logging_dir", str(log_dir),
    ]


def run_training(repo_root: Path, cmd: list[str]) -> int:
    for need in (repo_root / "venv-train" / "bin" / "accelerate", repo_root / "sd-scripts"):
        if not need.exists():
            sys.exit(f"{need} がありません。先に  bash scripts/04_install_lora_trainer.sh  を実行してください。")
    # ROCm on WSL の環境変数を env.sh から読み込んだうえで実行する
    shell = (
        f"export VENV_DIR={shlex.quote(str(repo_root / 'venv-train'))}; "
        f"source {shlex.quote(str(repo_root / 'scripts' / 'env.sh'))}; "
        "export TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1 MIOPEN_FIND_MODE=FAST; "
        f"cd {shlex.quote(str(repo_root / 'sd-scripts'))} && exec " + " ".join(shlex.quote(c) for c in cmd)
    )
    return subprocess.call(["bash", "-c", shell])
