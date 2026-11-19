# shellcheck shell=bash
# 共通の環境設定。他のスクリプトから `source scripts/env.sh` で読み込む。
# 手動で使う場合も:  source scripts/env.sh

_ENV_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export REPO_ROOT="${REPO_ROOT:-$(cd "${_ENV_SH_DIR}/.." && pwd)}"
export SBV2_DIR="${SBV2_DIR:-${REPO_ROOT}/Style-Bert-VITS2}"
export VENV_DIR="${VENV_DIR:-${REPO_ROOT}/venv}"

# uv のインストール先（~/.local/bin）を PATH に入れる
case ":${PATH}:" in
  *":${HOME}/.local/bin:"*) ;;
  *) export PATH="${HOME}/.local/bin:${PATH}" ;;
esac

# --- ROCm on WSL2 -----------------------------------------------------------
# WSL2 では GPU は /dev/kfd ではなく /dev/dxg 経由で見える。
export HSA_ENABLE_DXG_DETECTION="${HSA_ENABLE_DXG_DETECTION:-1}"
# GPU が認識されない場合のみ、次の行のコメントを外す（RX 9070 XT = gfx1201）。
# export HSA_OVERRIDE_GFX_VERSION=12.0.1

# PyTorch >= 2.6 は torch.load の既定が weights_only=True になり、
# Style-Bert-VITS2 の一部チェックポイント読み込みが失敗するため無効化する。
# （自分で学習した / 公式から取得したファイルだけを読み込む前提）
export TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1

if [[ -f "${VENV_DIR}/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
fi
