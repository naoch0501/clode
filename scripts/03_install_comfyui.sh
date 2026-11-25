#!/usr/bin/env bash
# ComfyUI + IP-Adapter (ComfyUI_IPAdapter_plus) + 必要なモデルを入れる。
# 使い方:  bash scripts/03_install_comfyui.sh
#
# ComfyUI は Style-Bert-VITS2 とは別の venv (venv-comfyui/) に入れる（依存の衝突を避けるため）。
# PyTorch は 02 で入れたものと「同じ wheel」を入れるので、uv のキャッシュが使われ
# 数 GB の再ダウンロードは発生しない（torchvision だけ追加で数 MB 取得する）。
#
# 主な環境変数（すべて省略可）:
#   ROCM_VERSION=7.2.1     PyTorch wheel の取得先（02 と同じ値にする）
#   TORCH_SOURCE=radeon    radeon: repo.radeon.com / pytorch: download.pytorch.org
#   PYTORCH_ROCM_INDEX=https://download.pytorch.org/whl/rocm6.4
#   PY_VER=                venv の Python。省略時は 02 の venv と同じバージョン（キャッシュを効かせるため）
#   COMFY_REF=master       ComfyUI のブランチ/タグ
#   WITH_MANAGER=1         ComfyUI-Manager も入れる (0 で入れない)
#   CHECKPOINT_URL=        画像生成モデル (.safetensors) の URL。指定すると models/checkpoints/ に保存
#   CHECKPOINT_NAME=       その保存ファイル名（省略時は URL から推測）
#   CIVITAI_TOKEN=         Civitai からダウンロードする場合の API キー
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SBV2_VENV="${REPO_ROOT}/venv"
export COMFY_DIR="${COMFY_DIR:-${REPO_ROOT}/ComfyUI}"
export COMFY_VENV="${COMFY_VENV:-${REPO_ROOT}/venv-comfyui}"
# env.sh が SBV2 の venv を有効化しないよう、先に VENV_DIR を ComfyUI 用に向ける
export VENV_DIR="${COMFY_VENV}"
# shellcheck source=scripts/env.sh
source "${SCRIPT_DIR}/env.sh"

ROCM_VERSION="${ROCM_VERSION:-7.2.1}"
TORCH_SOURCE="${TORCH_SOURCE:-radeon}"
PYTORCH_ROCM_INDEX="${PYTORCH_ROCM_INDEX:-https://download.pytorch.org/whl/rocm6.4}"
COMFY_REF="${COMFY_REF:-master}"
WITH_MANAGER="${WITH_MANAGER:-1}"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> uv をインストール"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

# --- 02 の venv から torch の情報を読む ---------------------------------------
SBV2_TORCH=""
if [[ -x "${SBV2_VENV}/bin/python" ]]; then
  SBV2_TORCH="$("${SBV2_VENV}/bin/python" -c 'import torch; print(torch.__version__ if torch.version.hip else "")' 2>/dev/null || true)"
  if [[ -z "${PY_VER:-}" ]]; then
    PY_VER="$("${SBV2_VENV}/bin/python" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  fi
fi
PY_VER="${PY_VER:-3.12}"
if [[ -n "${SBV2_TORCH}" ]]; then
  echo "==> 02 の venv の PyTorch を再利用: ${SBV2_TORCH} (Python ${PY_VER})"
else
  echo "==> 02 の venv に ROCm 版 PyTorch が見つからないため、新規にダウンロードします (Python ${PY_VER})"
fi
TORCH_BASE_VER="${SBV2_TORCH%%+*}"
# torch と対になるバージョン（torchvision は 0.(torch のマイナー+15).パッチ、torchaudio は torch と同じ）
TV_VER=""
if [[ "${TORCH_BASE_VER}" =~ ^2\.([0-9]+)\.([0-9]+) ]]; then
  TV_VER="0.$((BASH_REMATCH[1] + 15)).${BASH_REMATCH[2]}"
fi

# --- venv --------------------------------------------------------------------
if [[ ! -x "${COMFY_VENV}/bin/python" ]]; then
  echo "==> venv (Python ${PY_VER}) を作成: ${COMFY_VENV}"
  uv venv --python "${PY_VER}" "${COMFY_VENV}"
fi
# shellcheck disable=SC1091
source "${COMFY_VENV}/bin/activate"
PY_TAG="cp$(python -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")')"

# --- PyTorch (ROCm) + torchvision ---------------------------------------------
pick_wheel() {  # $1=listing $2=package名 $3=優先するバージョン(空可)
  local all
  all="$(grep -oE "${2}-[0-9][^\"<>]*-${PY_TAG}-${PY_TAG}-[^\"<>]*\.whl" <<<"$1" | sort -uV || true)"
  if [[ -n "$3" ]] && grep -q -- "${2}-${3}[+%-]" <<<"${all}"; then
    grep -- "${2}-${3}[+%-]" <<<"${all}" | tail -n1
  else
    tail -n1 <<<"${all}"
  fi
}

install_torch_radeon() {
  local base="https://repo.radeon.com/rocm/manylinux/rocm-rel-${ROCM_VERSION}"
  local listing torch_whl tv_whl ta_whl triton_whl wheels=()
  listing="$(curl -fsSL "${base}/" || true)"
  torch_whl="$(pick_wheel "${listing}" torch "${TORCH_BASE_VER}")"
  tv_whl="$(pick_wheel "${listing}" torchvision "${TV_VER}")"
  ta_whl="$(pick_wheel "${listing}" torchaudio "${TORCH_BASE_VER}")"
  [[ -z "${torch_whl}" || -z "${tv_whl}" ]] && return 1
  wheels+=("${base}/${torch_whl}" "${base}/${tv_whl}")
  [[ -n "${ta_whl}" ]] && wheels+=("${base}/${ta_whl}")
  for pkg in pytorch_triton_rocm triton; do
    triton_whl="$(pick_wheel "${listing}" "${pkg}" "")"
    [[ -n "${triton_whl}" ]] && wheels+=("${base}/${triton_whl}")
  done
  echo "==> AMD 公式 wheel をインストール（02 と同じものは uv キャッシュから）:"
  printf '    %s\n' "${wheels[@]}"
  uv pip install "${wheels[@]}"
}

install_torch_pytorch() {
  echo "==> download.pytorch.org (${PYTORCH_ROCM_INDEX}) から torch/torchvision/torchaudio をインストール"
  if [[ -n "${TORCH_BASE_VER}" ]]; then
    uv pip install "torch==${TORCH_BASE_VER}" "torchvision==${TV_VER}" "torchaudio==${TORCH_BASE_VER}" --index-url "${PYTORCH_ROCM_INDEX}" \
      || uv pip install torch torchvision torchaudio --index-url "${PYTORCH_ROCM_INDEX}"
  else
    uv pip install torch torchvision torchaudio --index-url "${PYTORCH_ROCM_INDEX}"
  fi
}

if python -c 'import torch, torchvision, sys; sys.exit(0 if torch.version.hip else 1)' 2>/dev/null; then
  echo "==> ROCm 版 PyTorch は導入済み: $(python -c 'import torch; print(torch.__version__)')"
elif [[ "${TORCH_SOURCE}" == "radeon" ]] && install_torch_radeon; then
  :
else
  [[ "${TORCH_SOURCE}" == "radeon" ]] && echo "    repo.radeon.com に ${PY_TAG} 用 wheel が揃わないため pytorch.org にフォールバック"
  install_torch_pytorch
fi

# WSL では torch 同梱の libhsa-runtime64 を消し、システムの (WSL 対応) ランタイムを使わせる
if grep -qi microsoft /proc/version; then
  TORCH_LIB="$(python -c 'import os, torch; print(os.path.join(os.path.dirname(torch.__file__), "lib"))')"
  if compgen -G "${TORCH_LIB}/libhsa-runtime64.so*" >/dev/null; then
    echo "==> WSL 用に ${TORCH_LIB}/libhsa-runtime64.so* を削除"
    rm -f "${TORCH_LIB}"/libhsa-runtime64.so*
  fi
fi

install_requirements() {  # torch 系を除外して requirements.txt を入れる
  local req="$1" filtered
  [[ -f "${req}" ]] || return 0
  filtered="$(mktemp)"
  grep -viE '^[[:space:]]*(torch|torchaudio|torchvision)([[:space:]<>=!~;\[]|$)' "${req}" > "${filtered}"
  uv pip install -r "${filtered}"
  rm -f "${filtered}"
}

clone_or_update() {  # $1=url $2=dir $3=ref
  if [[ ! -d "$2/.git" ]]; then
    git clone "$1" "$2"
  fi
  git -C "$2" fetch --quiet origin "$3" || true
  git -C "$2" checkout --quiet "$3" || true
  git -C "$2" pull --quiet --ff-only origin "$3" 2>/dev/null || true
}

# --- ComfyUI 本体 ------------------------------------------------------------
echo "==> ComfyUI を取得: ${COMFY_DIR}"
clone_or_update https://github.com/comfyanonymous/ComfyUI.git "${COMFY_DIR}" "${COMFY_REF}"
echo "==> ComfyUI の依存パッケージ（torch 系は除外して ROCm 版を維持）"
install_requirements "${COMFY_DIR}/requirements.txt"

# --- カスタムノード ------------------------------------------------------------
NODES="${COMFY_DIR}/custom_nodes"
echo "==> ComfyUI_IPAdapter_plus を取得"
clone_or_update https://github.com/cubiq/ComfyUI_IPAdapter_plus.git "${NODES}/ComfyUI_IPAdapter_plus" main
install_requirements "${NODES}/ComfyUI_IPAdapter_plus/requirements.txt"
if [[ "${WITH_MANAGER}" == "1" ]]; then
  echo "==> ComfyUI-Manager を取得"
  clone_or_update https://github.com/Comfy-Org/ComfyUI-Manager.git "${NODES}/ComfyUI-Manager" main
  install_requirements "${NODES}/ComfyUI-Manager/requirements.txt"
fi

if ! python -c 'import torch, sys; sys.exit(0 if torch.version.hip else 1)'; then
  echo "エラー: 依存解決で torch が ROCm 版ではなくなりました。TORCH_SOURCE を変えて再実行してください。" >&2
  exit 1
fi

# --- モデル ------------------------------------------------------------------
download() {  # $1=url $2=保存先  （既にあればスキップ、途中からの再開に対応）
  if [[ -s "$2" ]]; then
    echo "    スキップ（取得済み）: ${2#"${COMFY_DIR}/"}"
    return 0
  fi
  echo "    取得: ${2#"${COMFY_DIR}/"}"
  mkdir -p "$(dirname "$2")"
  curl -fL --retry 3 -C - -o "$2.part" "$1"
  mv "$2.part" "$2"
}

M="${COMFY_DIR}/models"
echo "==> IP-Adapter / CLIP Vision モデル（合計 約 3.5GB）"
download https://huggingface.co/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors \
  "${M}/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors"
download https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors \
  "${M}/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"

if [[ -n "${CHECKPOINT_URL:-}" ]]; then
  url="${CHECKPOINT_URL}"
  if [[ "${url}" == *civitai.com* && -n "${CIVITAI_TOKEN:-}" ]]; then
    [[ "${url}" == *\?* ]] && url="${url}&token=${CIVITAI_TOKEN}" || url="${url}?token=${CIVITAI_TOKEN}"
  fi
  name="${CHECKPOINT_NAME:-$(basename "${CHECKPOINT_URL%%\?*}")}"
  [[ "${name}" == *.safetensors ]] || name="${name}.safetensors"
  echo "==> 画像生成モデル"
  download "${url}" "${M}/checkpoints/${name}"
fi

echo
echo "==> GPU の確認"
python "${SCRIPT_DIR}/check_gpu.py" || echo "警告: GPU 確認に失敗しました。docs/troubleshooting.md を参照してください。" >&2

echo
echo "完了。"
if ! compgen -G "${M}/checkpoints/*.safetensors" >/dev/null; then
  echo "  画像生成モデル (.safetensors) を ${M}/checkpoints/ に置いてください。"
fi
echo "  起動:      bash scripts/comfyui.sh      → ブラウザで http://localhost:8188"
