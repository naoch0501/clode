#!/usr/bin/env bash
# WSL2 (Ubuntu 24.04) に ROCm を入れる。
# 使い方:  bash scripts/01_install_rocm_wsl.sh
#   ROCM_VERSION=7.2.1 bash scripts/01_install_rocm_wsl.sh   # バージョン指定
#
# AMD 公式の「Install Radeon software for WSL with ROCm」の手順を自動化したもの。
# 事前に Windows 側へ最新の AMD Software: Adrenalin Edition を入れておくこと。
set -euo pipefail

ROCM_VERSION="${ROCM_VERSION:-7.2.1}"
REPO_BASE="https://repo.radeon.com/amdgpu-install/${ROCM_VERSION}/ubuntu"

if ! grep -qi microsoft /proc/version; then
  echo "警告: WSL 上ではないようです。このスクリプトは WSL2 用です。" >&2
fi

# shellcheck disable=SC1091
. /etc/os-release
CODENAME="${VERSION_CODENAME:-noble}"
echo "==> Ubuntu ${VERSION_ID:-?} (${CODENAME}) / ROCm ${ROCM_VERSION}"

sudo apt-get update
sudo apt-get install -y wget curl ca-certificates git build-essential ffmpeg libsndfile1

# amdgpu-install の .deb 名はリリースごとに変わるので、ディレクトリ一覧から探す
DEB_NAME="$(curl -fsSL "${REPO_BASE}/${CODENAME}/" \
  | grep -oE 'amdgpu-install_[^"<>]+_all\.deb' | sort -u | tail -n1 || true)"
if [[ -z "${DEB_NAME}" ]]; then
  echo "エラー: ${REPO_BASE}/${CODENAME}/ に amdgpu-install が見つかりません。" >&2
  echo "       https://repo.radeon.com/amdgpu-install/ で存在するバージョンを確認し、" >&2
  echo "       ROCM_VERSION=x.y.z を指定して再実行してください。" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
echo "==> ${DEB_NAME} をダウンロード"
wget -q -O "${TMP_DIR}/${DEB_NAME}" "${REPO_BASE}/${CODENAME}/${DEB_NAME}"
sudo apt-get install -y "${TMP_DIR}/${DEB_NAME}"

# ROCm 7.2 以降は `--usecase=wsl` が廃止された。通常の rocm を入れ、
# WSL 用には librocdxg（/dev/dxg 経由で GPU を使うためのライブラリ）を別途入れる。
echo "==> ROCm をインストール"
sudo amdgpu-install -y --usecase=rocm --no-dkms

# librocdxg のバージョンは ROCm に合わせる（1.2.0 = ROCm 7.2.x 用）
ROCDXG_VERSION="${ROCDXG_VERSION:-1.2.0}"
if dpkg -s rocdxg-roct >/dev/null 2>&1; then
  echo "==> librocdxg は導入済み: $(dpkg-query -W -f='${Version}' rocdxg-roct)"
else
  echo "==> librocdxg ${ROCDXG_VERSION} をインストール"
  ROCDXG_DEB="rocdxg-roct_${ROCDXG_VERSION}_amd64.deb"
  wget -q -O "${TMP_DIR}/${ROCDXG_DEB}" \
    "https://github.com/ROCm/librocdxg/releases/download/v${ROCDXG_VERSION}/${ROCDXG_DEB}"
  sudo dpkg -i "${TMP_DIR}/${ROCDXG_DEB}" || true
  sudo apt-get install -f -y
fi

sudo usermod -aG render,video "${USER}" || true

# ~/.bashrc に環境変数を追記（冪等）
MARK="# >>> voice-tts rocm-wsl >>>"
if ! grep -qF "${MARK}" "${HOME}/.bashrc" 2>/dev/null; then
  cat >> "${HOME}/.bashrc" <<'EOF'
# >>> voice-tts rocm-wsl >>>
export HSA_ENABLE_DXG_DETECTION=1
# GPU が見えない場合のみ有効化:  export HSA_OVERRIDE_GFX_VERSION=12.0.1
# <<< voice-tts rocm-wsl <<<
EOF
fi

echo
echo "==> 確認 (rocminfo)"
export HSA_ENABLE_DXG_DETECTION=1
if rocminfo 2>/dev/null | grep -E 'Marketing Name|gfx1' | head -n 6; then
  echo
  echo "完了。いったん WSL を再起動してください（PowerShell で: wsl --shutdown）。"
  echo "次:  bash scripts/02_install_sbv2.sh"
else
  echo "rocminfo で GPU が確認できませんでした。docs/troubleshooting.md を参照してください。" >&2
  exit 1
fi
