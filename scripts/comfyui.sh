#!/usr/bin/env bash
# ComfyUI を起動する（RX 9070 XT / ROCm on WSL2 向けの設定込み）。
# 使い方:  bash scripts/comfyui.sh [ComfyUI の追加オプション...]
#   例:  bash scripts/comfyui.sh --fp32-vae        # 真っ黒な画像が出るとき
#        bash scripts/comfyui.sh --lowvram         # VRAM 不足のとき
#
# 環境変数:
#   COMFY_PORT=8188  COMFY_LISTEN=127.0.0.1
#   AOTRITON=1       PyTorch の高速 attention (AOTriton) を RDNA4 で有効化。不安定なら 0
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export COMFY_DIR="${COMFY_DIR:-${REPO_ROOT}/ComfyUI}"
export VENV_DIR="${COMFY_VENV:-${REPO_ROOT}/venv-comfyui}"
# shellcheck source=scripts/env.sh
source "${SCRIPT_DIR}/env.sh"

if [[ ! -f "${COMFY_DIR}/main.py" ]]; then
  echo "ComfyUI がありません。先に  bash scripts/03_install_comfyui.sh  を実行してください。" >&2
  exit 1
fi

if [[ "${AOTRITON:-1}" == "1" ]]; then
  export TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1
fi
# 初回の畳み込みカーネル探索を短くする（初回起動・初回生成が遅いのを緩和）
export MIOPEN_FIND_MODE="${MIOPEN_FIND_MODE:-FAST}"

cd "${COMFY_DIR}"
exec python main.py \
  --listen "${COMFY_LISTEN:-127.0.0.1}" --port "${COMFY_PORT:-8188}" \
  --use-pytorch-cross-attention "$@"
