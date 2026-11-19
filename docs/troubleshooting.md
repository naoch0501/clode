# トラブルシューティング

## `ERROR: Usecase implementation 'wsl' is not supported or invalid`
ROCm 7.2 以降で `amdgpu-install --usecase=wsl` が廃止されたため。最新の `scripts/01_install_rocm_wsl.sh`
（`--usecase=rocm` + librocdxg を導入）を `git pull` してから再実行する。

## `Failed to build av==10.0.0`（faster-whisper の依存）
faster-whisper は CUDA 専用で、Ubuntu 24.04 ではビルドもできない。最新の `scripts/02_install_sbv2.sh` は
これを除外して入れる。文字起こしは HF Whisper（`--use_hf_whisper`）を使う。

## GPU が認識されない（`rocminfo` に出ない / `check_gpu.py` が NG）
1. Windows 側の Adrenalin ドライバを最新にし、PowerShell で `wsl --update` → `wsl --shutdown`。
2. 環境変数を確認する: `echo $HSA_ENABLE_DXG_DETECTION` → `1` であること（`source scripts/env.sh` で設定される）。
3. それでも出なければ `scripts/env.sh` の次の行のコメントを外す:
   ```bash
   export HSA_OVERRIDE_GFX_VERSION=12.0.1
   ```
4. `python -c "import torch; print(torch.__version__, torch.version.hip)"` で `hip` が `None` なら CUDA 版 / CPU 版の torch が入っている。
   `uv pip uninstall torch torchaudio` のあと `bash scripts/02_install_sbv2.sh` を再実行する。
5. torch は見えるが `HSA` 関連のエラーが出る場合は、torch 同梱の `libhsa-runtime64.so` が残っている可能性がある。
   `02_install_sbv2.sh` を再実行すると削除される。
6. ROCm のバージョンを変えて試す場合は `ROCM_VERSION=x.y.z` を付けて 01 → 02 を実行し直す。
   PyTorch wheel を pytorch.org から入れる場合は `TORCH_SOURCE=pytorch` を付ける。

## `Weights only load failed` / `weights_only` のエラー
PyTorch 2.6 以降の仕様変更による。`scripts/env.sh` で `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` を設定しているので、
**`source scripts/env.sh` を忘れていないか** 確認する（`train.sh` / 各スクリプトは自動で読み込む）。
WebUI (`python app.py`) を直接起動するときも、先に `source scripts/env.sh` を実行すること。

## `HIP out of memory`（VRAM 不足）
- `BATCH=2 bash scripts/train.sh train myvoice` のようにバッチサイズを下げる。
- 文字起こしで落ちる場合は `WHISPER_MODEL=medium` にする。
- 他の GPU を使うアプリ（ゲーム・ブラウザのハードウェアアクセラレーション等）を閉じる。

## 学習中に WSL ごと落ちる / `Killed`
メインメモリ不足。`docs/windows-setup.md` の `.wslconfig` で `memory` と `swap` を増やす。
前処理の並列数を減らすのも有効: `Style-Bert-VITS2` で `python preprocess_all.py ... --num_processes 2`。

## 学習中に NaN が出る / 音が壊れる
- `Style-Bert-VITS2/Data/<モデル名>/config.json` の `"bf16_run"` が `false` になっているか確認する（既定は false）。
- `"fp16_run"` があれば `false` にする。

## 読みがおかしい
- `esd.list` の文字起こしを見直す（`docs/recording-tips.md`）。
- 固有名詞の読みは WebUI のエディタ（`python server_editor.py --inbrowser`）の「ユーザー辞書」で登録できる。

## 学習を途中から再開したい
```bash
source scripts/env.sh
cd Style-Bert-VITS2
python train_ms_jp_extra.py --skip_default_style
```

## WebUI がブラウザで開けない
WSL2 の `localhost` は Windows から自動で転送される。`http://localhost:7860`（WebUI）や
`http://localhost:8000`（エディタ）を Windows のブラウザで開く。開けない場合は `--host 0.0.0.0` を付けて起動し、
WSL の IP（`hostname -I`）で開く。
