# 自分の声で喋る音声合成環境

自分の声の録音を学習させ、好きな文章をその声で読み上げさせるための環境です。

- **音声合成エンジン**: [Style-Bert-VITS2](https://github.com/litagin02/Style-Bert-VITS2)（JP-Extra / 日本語特化）
- **想定環境**: Windows 11 + **AMD Radeon RX 9070 XT** + WSL2 (Ubuntu 24.04) + ROCm 版 PyTorch

> ⚠️ 学習に使ってよいのは **自分の声、または本人から明確な許可を得た声** だけです。
> 作った音声を他人の発言として公開するなど、誤解や被害を招く使い方はしないでください。

## 全体の流れ

```
録音ファイル ─► 分割・文字起こし ─► (文字起こしを確認・修正) ─► 学習 ─► 文章を読み上げ
 inputs/<名前>/   train.sh prepare        Data/<名前>/esd.list      train.sh train   speak.py
```

## 1. Windows 側の準備
[docs/windows-setup.md](docs/windows-setup.md) の手順で、次を済ませます。
- AMD Adrenalin ドライバを最新にする
- `wsl --install -d Ubuntu-24.04`
- `.wslconfig` で WSL のメモリ割り当てを増やす

## 2. WSL (Ubuntu) でセットアップ

```bash
git clone https://github.com/naoch0501/clode.git ~/clode
cd ~/clode

bash scripts/01_install_rocm_wsl.sh     # ROCm を入れる（sudo パスワードを聞かれる）
# → PowerShell で  wsl --shutdown  して Ubuntu を開き直す

bash scripts/02_install_sbv2.sh         # PyTorch(ROCm) + Style-Bert-VITS2 を venv に入れる（数 GB ダウンロード）

source scripts/env.sh
python scripts/check_gpu.py             # 「GPU: AMD Radeon RX 9070 XT」「OK」と出れば成功
```

うまくいかない場合は [docs/troubleshooting.md](docs/troubleshooting.md) を参照してください。

## 3. 学習

録音ファイルを `inputs/<モデル名>/` に置きます（例: `inputs/myvoice/rec01.wav`）。
素材の作り方は [docs/recording-tips.md](docs/recording-tips.md) を参照してください。

```bash
# 自動分割 + Whisper で自動文字起こし
bash scripts/train.sh prepare myvoice

# Style-Bert-VITS2/Data/myvoice/esd.list を開いて文字起こしの誤りを直す（品質に直結します）

# 前処理 + 学習（数時間かかります）
bash scripts/train.sh train myvoice
```

エポック数などは環境変数で変えられます: `EPOCHS=200 BATCH=2 bash scripts/train.sh train myvoice`

学習の進み具合は別のターミナルで TensorBoard を開くと確認できます:

```bash
source scripts/env.sh && tensorboard --logdir Style-Bert-VITS2/Data/myvoice/models
# Windows のブラウザで http://localhost:6006
```

## 4. 喋らせる

```bash
source scripts/env.sh
python scripts/speak.py -m myvoice --text "こんにちは。これは私の声で合成した音声です。" --out hello.wav
python scripts/speak.py -m myvoice --file 原稿.txt --out 原稿.wav --speed 1.1
python scripts/speak.py -m myvoice --list-styles
```

### GUI で使う

```bash
source scripts/env.sh
cd Style-Bert-VITS2
python server_editor.py      # 読み上げエディタ → Windows のブラウザで http://localhost:8000
python app.py                # 学習・スタイル作成・マージなどの WebUI → http://localhost:7860
```

エディタではアクセントの調整やユーザー辞書の登録ができます。WebUI の「スタイル作成」で、
喜び・怒りなどの話し方ごとのスタイルを作れます（素材にそういう話し方が含まれている場合）。

## ファイル構成

| パス | 内容 |
|---|---|
| `scripts/01_install_rocm_wsl.sh` | WSL2 に ROCm をインストール |
| `scripts/02_install_sbv2.sh` | venv 作成、ROCm 版 PyTorch・Style-Bert-VITS2 のインストール |
| `scripts/env.sh` | venv と ROCm 用環境変数の読み込み（各スクリプトが自動で読み込む） |
| `scripts/check_gpu.py` | GPU が学習に使えるかの確認 |
| `scripts/train.sh` | 分割・文字起こし・前処理・学習 |
| `scripts/speak.py` | テキスト → wav |
| `inputs/<名前>/` | 録音ファイル置き場（git 管理外） |
| `Style-Bert-VITS2/` | 本体（02 で clone、git 管理外）。学習データは `Data/`、完成モデルは `model_assets/` |

## 補足: AMD GPU で動かすための工夫
Style-Bert-VITS2 は公式には NVIDIA (CUDA) 向けです。このリポジトリでは次の対応をしています。
- PyTorch は AMD 公式 (repo.radeon.com) の ROCm 版を先に入れ、Style-Bert-VITS2 の `requirements.txt` からは torch を除外して入れる
- WSL 用に torch 同梱の `libhsa-runtime64.so` を削除し、`HSA_ENABLE_DXG_DETECTION=1` を設定
- 新しい PyTorch で起きる `torch.load` の `weights_only` エラーを `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` で回避
- 文字起こしは CUDA 専用の faster-whisper ではなく、Hugging Face 版 Whisper (`--use_hf_whisper`) を使う
