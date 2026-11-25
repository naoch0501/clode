#!/usr/bin/env bash
# キャラクター LoRA 学習用に kohya-ss/sd-scripts を入れる。
# 使い方:  bash scripts/04_install_lora_trainer.sh
#
# 学習用の venv (venv-train/) は ComfyUI とは別にする（diffusers などのバージョンが衝突するため）。
# PyTorch は venv-comfyui と「同じバージョン・同じ取得元」で入れるので uv のキャッシュが使われ、
# 再ダウンロードは発生しない。
#
# 環境変数（省略可）:
#   SDSCRIPTS_REF=main     sd-scripts のブランチ/タグ
#   ROCM_VERSION=7.2.1     PyTorch が repo.radeon.com 由来のときの取得先
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMFY_VENV="${COMFY_VENV:-${REPO_ROOT}/venv-comfyui}"
export TRAIN_VENV="${TRAIN_VENV:-${REPO_ROOT}/venv-train}"
export SDSCRIPTS_DIR="${SDSCRIPTS_DIR:-${REPO_ROOT}/sd-scripts}"
export VENV_DIR="${TRAIN_VENV}"
# shellcheck source=scripts/env.sh
source "${SCRIPT_DIR}/env.sh"

SDSCRIPTS_REF="${SDSCRIPTS_REF:-main}"
ROCM_VERSION="${ROCM_VERSION:-7.2.1}"
export PATH="${HOME}/.local/bin:${PATH}"
command -v uv >/dev/null || { echo "uv がありません。先に scripts/03_install_comfyui.sh を実行してください。" >&2; exit 1; }
[[ -x "${COMFY_VENV}/bin/python" ]] || { echo "${COMFY_VENV} がありません。先に scripts/03_install_comfyui.sh を実行してください。" >&2; exit 1; }

# --- venv-comfyui の torch 情報 --------------------------------------------------
read -r PY_VER TORCH_VER TV_VER < <("${COMFY_VENV}/bin/python" - <<'PY'
import sys, importlib.metadata as m
print(f"{sys.version_info[0]}.{sys.version_info[1]}", m.version("torch"), m.version("torchvision"))
PY
)
echo "==> venv-comfyui の PyTorch を再利用: torch ${TORCH_VER} / torchvision ${TV_VER} (Python ${PY_VER})"

if [[ ! -x "${TRAIN_VENV}/bin/python" ]]; then
  echo "==> venv (Python ${PY_VER}) を作成: ${TRAIN_VENV}"
  uv venv --python "${PY_VER}" "${TRAIN_VENV}"
fi
# shellcheck disable=SC1091
source "${TRAIN_VENV}/bin/activate"
PY_TAG="cp${PY_VER/./}"

if python -c 'import torch, sys; sys.exit(0 if torch.version.hip else 1)' 2>/dev/null; then
  echo "==> ROCm 版 PyTorch は導入済み"
else
  LOCAL="${TORCH_VER#*+}"
  if [[ "${LOCAL}" =~ ^rocm[0-9.]+$ ]]; then
    # download.pytorch.org 由来（例: 2.8.0+rocm6.4）
    uv pip install "torch==${TORCH_VER}" "torchvision==${TV_VER}" \
      --index-url "https://download.pytorch.org/whl/${LOCAL}"
  else
    # repo.radeon.com 由来
    base="https://repo.radeon.com/rocm/manylinux/rocm-rel-${ROCM_VERSION}"
    listing="$(curl -fsSL "${base}/")"
    wheels=()
    for spec in "torch:${TORCH_VER%%+*}" "torchvision:${TV_VER%%+*}" "pytorch_triton_rocm:" "triton:"; do
      pkg="${spec%%:*}"; ver="${spec#*:}"
      name="$(grep -oE "${pkg}-${ver}[^\"<>]*-${PY_TAG}-${PY_TAG}-[^\"<>]*\.whl" <<<"${listing}" | sort -uV | tail -n1 || true)"
      [[ -n "${name}" ]] && wheels+=("${base}/${name}")
    done
    printf '    %s\n' "${wheels[@]}"
    uv pip install "${wheels[@]}"
  fi
fi

if grep -qi microsoft /proc/version; then
  TORCH_LIB="$(python -c 'import os, torch; print(os.path.join(os.path.dirname(torch.__file__), "lib"))')"
  if compgen -G "${TORCH_LIB}/libhsa-runtime64.so*" >/dev/null; then
    echo "==> WSL 用に ${TORCH_LIB}/libhsa-runtime64.so* を削除"
    rm -f "${TORCH_LIB}"/libhsa-runtime64.so*
  fi
fi

# --- sd-scripts ----------------------------------------------------------------
if [[ ! -d "${SDSCRIPTS_DIR}/.git" ]]; then
  echo "==> sd-scripts を clone"
  git clone https://github.com/kohya-ss/sd-scripts.git "${SDSCRIPTS_DIR}"
fi
git -C "${SDSCRIPTS_DIR}" fetch --quiet origin "${SDSCRIPTS_REF}" || true
git -C "${SDSCRIPTS_DIR}" checkout --quiet "${SDSCRIPTS_REF}" || true
git -C "${SDSCRIPTS_DIR}" pull --quiet --ff-only origin "${SDSCRIPTS_REF}" 2>/dev/null || true

echo "==> sd-scripts の依存（torch 系・CUDA 専用の xformers / bitsandbytes は除外）"
REQ="${SDSCRIPTS_DIR}/requirements.rocm.txt"   # "-e ." を解決するため sd-scripts 内に置く
grep -viE '^[[:space:]]*(torch|torchaudio|torchvision|xformers|bitsandbytes)([[:space:]<>=!~;\[]|$)' \
  "${SDSCRIPTS_DIR}/requirements.txt" > "${REQ}"
(cd "${SDSCRIPTS_DIR}" && uv pip install -r "${REQ}")

if ! python -c 'import torch, sys; sys.exit(0 if torch.version.hip else 1)'; then
  echo "エラー: 依存解決で torch が ROCm 版ではなくなりました。" >&2
  exit 1
fi

echo "==> 動作確認"
python -c 'import torch, accelerate, diffusers, library.train_util; print("torch", torch.__version__, "/ GPU:", torch.cuda.get_device_name(0))' \
  || { echo "エラー: sd-scripts の読み込みに失敗しました。" >&2; exit 1; }

echo
echo "完了。次は illust/ で:  uv pip install -r requirements-lora.txt"
echo "                       python -m illustgen lora-prep -c characters/<名前>"
