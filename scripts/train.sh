#!/usr/bin/env bash
# 自分の声で Style-Bert-VITS2 (JP-Extra) モデルを学習する。
#
# 使い方:
#   bash scripts/train.sh prepare <model_name>   # 音声の分割 + 自動文字起こし
#   （Data/<model_name>/esd.list を確認・修正）
#   bash scripts/train.sh train   <model_name>   # 前処理 + 学習
#   bash scripts/train.sh all     <model_name>   # 上の 2 つを続けて実行
#
# 録音ファイルは inputs/<model_name>/ に置く（wav/flac/mp3/m4a/ogg/opus）。
#
# 調整用の環境変数:
#   EPOCHS=100  BATCH=4  SAVE_EVERY=1000  WHISPER_MODEL=large-v3  YOMI_ERROR=skip
#   MIN_SEC=2   MAX_SEC=12
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/env.sh
source "${SCRIPT_DIR}/env.sh"

usage() { sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 1; }

STAGE="${1:-}"
MODEL="${2:-}"
[[ -z "${STAGE}" || -z "${MODEL}" ]] && usage
[[ "${MODEL}" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "モデル名は英数字・_・- のみ使えます" >&2; exit 1; }

EPOCHS="${EPOCHS:-100}"
BATCH="${BATCH:-4}"
SAVE_EVERY="${SAVE_EVERY:-1000}"
WHISPER_MODEL="${WHISPER_MODEL:-large-v3}"
YOMI_ERROR="${YOMI_ERROR:-skip}"
MIN_SEC="${MIN_SEC:-2}"
MAX_SEC="${MAX_SEC:-12}"

INPUT_DIR="${REPO_ROOT}/inputs/${MODEL}"
DATA_DIR="${SBV2_DIR}/Data/${MODEL}"

[[ -d "${SBV2_DIR}" ]] || { echo "Style-Bert-VITS2 がありません。先に scripts/02_install_sbv2.sh を実行。" >&2; exit 1; }
cd "${SBV2_DIR}"

prepare() {
  if [[ ! -d "${INPUT_DIR}" ]] || [[ -z "$(ls -A "${INPUT_DIR}" 2>/dev/null)" ]]; then
    echo "エラー: ${INPUT_DIR} に録音ファイルを置いてください。" >&2
    exit 1
  fi
  echo "==> [1/2] 無音区間で ${MIN_SEC}〜${MAX_SEC} 秒に分割: ${INPUT_DIR} -> ${DATA_DIR}/raw"
  python slice.py --model_name "${MODEL}" -i "${INPUT_DIR}" -m "${MIN_SEC}" -M "${MAX_SEC}"

  echo "==> [2/2] Whisper (${WHISPER_MODEL}) で文字起こし"
  python transcribe.py --model_name "${MODEL}" --device cuda --language ja \
    --use_hf_whisper --hf_repo_id "openai/whisper-${WHISPER_MODEL}" --batch_size 8

  local n
  n="$(wc -l < "${DATA_DIR}/esd.list")"
  echo
  echo "文字起こし完了: ${n} 件 -> ${DATA_DIR}/esd.list"
  echo "誤りがあれば修正してから train を実行してください（形式: ファイル名|話者名|JP|テキスト）。"
}

train() {
  [[ -f "${DATA_DIR}/esd.list" ]] || { echo "先に prepare を実行してください。" >&2; exit 1; }
  echo "==> [1/2] 前処理 (JP-Extra, epochs=${EPOCHS}, batch=${BATCH})"
  python preprocess_all.py -m "${MODEL}" --use_jp_extra \
    -b "${BATCH}" -e "${EPOCHS}" -s "${SAVE_EVERY}" \
    --normalize --trim --yomi_error "${YOMI_ERROR}"

  echo "==> [2/2] 学習開始（Ctrl+C で中断。再開は: python train_ms_jp_extra.py --skip_default_style）"
  python train_ms_jp_extra.py

  echo
  echo "学習完了。モデル: ${SBV2_DIR}/model_assets/${MODEL}/"
  echo "喋らせる:  python scripts/speak.py -m ${MODEL} --text \"こんにちは\""
}

case "${STAGE}" in
  prepare) prepare ;;
  train) train ;;
  all) prepare; train ;;
  *) usage ;;
esac
