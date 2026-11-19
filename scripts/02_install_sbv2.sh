#!/usr/bin/env bash
# Style-Bert-VITS2 と ROCm 版 PyTorch を venv に入れる。
# 使い方:  bash scripts/02_install_sbv2.sh
#
# 主な環境変数（すべて省略可）:
#   ROCM_VERSION=7.2.1     01 で入れた ROCm のバージョン（PyTorch wheel の取得先に使う）
#   PY_VER=3.10            venv の Python バージョン（Style-Bert-VITS2 の推奨は 3.10）
#   TORCH_SOURCE=radeon    radeon: repo.radeon.com の AMD 公式 wheel / pytorch: download.pytorch.org
#   PYTORCH_ROCM_INDEX=https://download.pytorch.org/whl/rocm6.4   TORCH_SOURCE=pytorch 時の index
#   SBV2_REF=master        Style-Bert-VITS2 のブランチ/タグ
#   TORCH_VERSION=2.8.0    PyTorch / torchaudio のバージョン（2.9 以降は音声分割が動かない）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/env.sh
source "${SCRIPT_DIR}/env.sh"

ROCM_VERSION="${ROCM_VERSION:-7.2.1}"
PY_VER="${PY_VER:-3.10}"
TORCH_SOURCE="${TORCH_SOURCE:-radeon}"
PYTORCH_ROCM_INDEX="${PYTORCH_ROCM_INDEX:-https://download.pytorch.org/whl/rocm6.4}"
SBV2_REF="${SBV2_REF:-master}"
TORCH_VERSION="${TORCH_VERSION:-2.8.0}"

# --- uv ----------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  echo "==> uv をインストール"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

# --- venv --------------------------------------------------------------------
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "==> venv (Python ${PY_VER}) を作成: ${VENV_DIR}"
  uv venv --python "${PY_VER}" "${VENV_DIR}"
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
PY_TAG="cp$(python -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")')"

# --- PyTorch (ROCm) ------------------------------------------------------------
# 2.8.0 に固定: torchaudio 2.9 で list_audio_backends などが削除され、
# 音声分割 (slice.py) が使う silero-vad が動かなくなるため。
install_torch_radeon() {
  local base="https://repo.radeon.com/rocm/manylinux/rocm-rel-${ROCM_VERSION}"
  if ! curl -fsSL "${base}/" | grep -qE "torch-${TORCH_VERSION//./\\.}%2B[^\"<>]*-${PY_TAG}-${PY_TAG}-"; then
    return 1
  fi
  echo "==> AMD 公式 wheel (torch/torchaudio ${TORCH_VERSION}, ${PY_TAG}) をインストール"
  # torch の依存は PyPI から先に入れ、torch 本体は --no-index で AMD のものだけを使う（CUDA 版の混入防止）
  uv pip install filelock fsspec jinja2 networkx sympy typing-extensions "numpy<2"
  uv pip install --no-index --find-links "${base}/" \
    --reinstall-package torch --reinstall-package torchaudio \
    "torch==${TORCH_VERSION}" "torchaudio==${TORCH_VERSION}"
}

install_torch_pytorch() {
  echo "==> download.pytorch.org (${PYTORCH_ROCM_INDEX}) から torch/torchaudio ${TORCH_VERSION} をインストール"
  uv pip install "torch==${TORCH_VERSION}" "torchaudio==${TORCH_VERSION}" --index-url "${PYTORCH_ROCM_INDEX}"
}

CURRENT_TORCH="$(python -c 'import torch; print(torch.__version__ if torch.version.hip else "")' 2>/dev/null || true)"
if [[ "${CURRENT_TORCH}" == "${TORCH_VERSION}+"* ]]; then
  echo "==> ROCm 版 PyTorch は導入済み: ${CURRENT_TORCH}"
else
  [[ -n "${CURRENT_TORCH}" ]] && echo "==> PyTorch ${CURRENT_TORCH} を ${TORCH_VERSION} に入れ替えます"
  if [[ "${TORCH_SOURCE}" == "radeon" ]]; then
    if ! install_torch_radeon; then
      echo "    repo.radeon.com に torch ${TORCH_VERSION} (${PY_TAG}) が見つからないため pytorch.org にフォールバック"
      install_torch_pytorch
    fi
  else
    install_torch_pytorch
  fi
fi

# WSL では torch 同梱の libhsa-runtime64 を消し、システムの (WSL 対応) ランタイムを使わせる
if grep -qi microsoft /proc/version; then
  TORCH_LIB="$(python -c 'import os, torch; print(os.path.join(os.path.dirname(torch.__file__), "lib"))')"
  if compgen -G "${TORCH_LIB}/libhsa-runtime64.so*" >/dev/null; then
    echo "==> WSL 用に ${TORCH_LIB}/libhsa-runtime64.so* を削除"
    rm -f "${TORCH_LIB}"/libhsa-runtime64.so*
  fi
fi

# --- Style-Bert-VITS2 --------------------------------------------------------
if [[ ! -d "${SBV2_DIR}/.git" ]]; then
  echo "==> Style-Bert-VITS2 を clone"
  git clone https://github.com/litagin02/Style-Bert-VITS2.git "${SBV2_DIR}"
fi
git -C "${SBV2_DIR}" fetch --quiet origin "${SBV2_REF}" || true
git -C "${SBV2_DIR}" checkout --quiet "${SBV2_REF}" || true

echo "==> 依存パッケージをインストール（torch/torchaudio は除外して ROCm 版を維持）"
# 除外するもの:
#   torch/torchaudio/torchvision : 上で入れた ROCm 版を CUDA 版で上書きさせない
#   faster-whisper : CUDA 専用で、依存の av==10 は Ubuntu 24.04 の ffmpeg ではビルドできない
#                    （HF Whisper を使うので不要。transcribe.py も使うときだけ import する）
#   transformers   : 無指定だと 5.x が入るため、下で 4.x に固定して入れる
REQ_FILTERED="$(mktemp)"
trap 'rm -f "${REQ_FILTERED}"' EXIT
grep -viE '^[[:space:]]*(torch|torchaudio|torchvision|faster-whisper|transformers)([[:space:]<>=!~;\[]|$)' \
  "${SBV2_DIR}/requirements.txt" > "${REQ_FILTERED}"
uv pip install -r "${REQ_FILTERED}"
# HF Whisper で文字起こしするため。Style-Bert-VITS2 は transformers 4.x 前提
# setuptools 81 以降は pkg_resources が無く、librosa 0.9 の import が失敗する
uv pip install "transformers<5" "accelerate" "setuptools<81"

# 依存解決で CUDA 版 torch に置き換わっていないか確認
if ! python -c 'import torch, sys; sys.exit(0 if torch.version.hip else 1)'; then
  echo "エラー: torch が ROCm 版ではなくなっています。TORCH_SOURCE を変えて再実行してください。" >&2
  exit 1
fi

echo "==> 初期化（BERT など必要なモデルをダウンロード）"
(cd "${SBV2_DIR}" && python initialize.py --skip_default_models)

echo
echo "完了。次:  python scripts/check_gpu.py"
