# illustgen — キャラクターデザインから成人向けイラストを生成

自作キャラクターのデザイン画(参考画像)を元に、そのキャラクターのイラスト(一般向け / R18)を
外部 GPU 上の **ComfyUI** で生成する CLI です。

```
characters/<名前>/            安全チェック          ComfyUI (自分のPC/RunPod)        outputs/<名前>/
  character.yaml  ─────────►  年齢・語句・写真判定 ─► IP-Adapter(参考画像) + LoRA ─► PNG + audit.jsonl
  design_front.png
```

## 利用ルール(ツール側で強制しています)

| ルール | 仕組み |
|---|---|
| 自作・権利を持つ **架空の** キャラクターのみ | `character.yaml` に `source: original` が必須 |
| **成人のみ** | `age` は 18 以上の数値が必須。プロンプトに常に `adult, mature` を追加し、ネガティブに未成年関連語を常に追加 |
| 未成年を連想させる語は使えない | `ロリ` `JK` `少女` `teen` `schoolgirl` などを含むプロンプトは生成前に停止 (`illustgen/safety.py`) |
| 実在人物の写真は使えない | `photo` `実写` `芸能人` などの語を拒否。カメラの EXIF がある参考画像を拒否 |
| 記録を残す | 全生成のプロンプト・seed・参考画像のハッシュを `outputs/audit.jsonl` に追記 |

自動チェックは完全ではありません。版権キャラクターや、実在人物を描き起こした画像を参考画像に使わないでください。
生成物の公開は、投稿先の規約と法令(日本ではわいせつ物頒布等の規制によるモザイク処理など)に従ってください。

## 1. ComfyUI を用意する

### A. 自分の PC (RX 9070 XT / WSL2) — おすすめ
音声合成用に `scripts/01_install_rocm_wsl.sh` と `02_install_sbv2.sh` を済ませてあれば、
02 の PyTorch を使い回すので、大きなダウンロードは IP-Adapter のモデル(約 3.5GB)だけです。

```bash
cd ~/clode
bash scripts/03_install_comfyui.sh     # ComfyUI + IP-Adapter ノード + モデル。最後に GPU 確認が「OK」と出れば成功
```

画像生成モデル(SDXL 系のアニメ調モデル。Illustrious XL / NoobAI XL 系など)を
`ComfyUI/models/checkpoints/` に置きます。URL が分かっていれば、ダウンロードも同時に行えます:
`CHECKPOINT_URL="https://civitai.com/api/download/models/<ID>" CIVITAI_TOKEN=<キー> bash scripts/03_install_comfyui.sh`

```bash
bash scripts/comfyui.sh          # 起動 → Windows のブラウザで http://localhost:8188
```
`config.yaml` の `comfyui.url` は `http://127.0.0.1:8188` にします。
真っ黒な画像が出る場合は `bash scripts/comfyui.sh --fp32-vae`、VRAM 不足なら `--lowvram` を付けます。

### B. RunPod(GPU を借りる)
手順は [docs/runpod-setup.md](docs/runpod-setup.md) にあります。Pod の URL
`https://<POD_ID>-8188.proxy.runpod.net` を `config.yaml` に書きます。

## 2. セットアップ

```bash
cd illust
uv venv .venv && source .venv/bin/activate     # uv は scripts/02 or 03 で導入済み
uv pip install -r requirements.txt
cp config.example.yaml config.yaml      # url と checkpoint を書き換える
python -m illustgen ping                # GPU 名が表示されれば接続 OK
```

## 3. キャラクターを登録する

`characters/example/` をコピーし、デザイン画を置いて `character.yaml` を書き換えます。

```bash
cp -r characters/example characters/mychar
cp ~/デザイン画/front.png characters/mychar/design_front.png
python -m illustgen check -c characters/mychar
```

- **参考画像**: 正面の全身立ち絵で、背景が無地のものが最もよく効きます。1枚目を使います。
- **appearance / outfit**: 髪色・目の色・服装などをタグで書きます。参考画像だけでは色や細部がぶれるので、タグでも指定してください。
- **LoRA**: 同じキャラクターを安定して出したい場合は、デザイン画と生成画像の良いものを 20〜40 枚集めてキャラ LoRA を学習し、`lora:` に指定するのが最も効果的です。

## 4. 生成する

```bash
# 一般向け
python -m illustgen generate -c characters/mychar -s "standing, smile, city street, night" -n 4

# R18
python -m illustgen generate -c characters/mychar -r r18 -s "lying on bed, bedroom, soft lighting" -n 4

# 参考画像の効き具合を調整(強いと構図まで似る、弱いと特徴が薄れる)
python -m illustgen generate -c characters/mychar -s "sitting, cafe" --weight 0.5 --seed 1234
```

| オプション | 内容 |
|---|---|
| `-s` | シーン・ポーズ・構図のタグ |
| `-r general / r18` | レーティング。既定は general |
| `-n` | 枚数(seed を 1 ずつ増やす) |
| `--weight` | 参考画像の強さ 0.0〜1.0(既定はキャラ定義の `reference_weight`) |
| `--start-at` / `--end-at` | 参考画像を効かせる区間 0.0〜1.0。色が濁るときは `--start-at 0.3` など |
| `--outfit "..."` / `--no-outfit` | この回だけ服装タグを差し替える / 入れない |
| `--no-reference` | 参考画像を使わない(LoRA だけで描く) |
| `--width --height --steps --cfg --checkpoint` | 設定ファイルの既定値を上書き |

## 5. キャラクター LoRA を作る(手元の GPU で学習)

参考画像(IP-Adapter)だけでは、キャップの形や髪型などの細部が安定しません。
キャラクターの LoRA を作ると、参考画像なしで同じキャラクターを描けるようになり、服装もタグで切り替えられます。

### 準備(最初の 1 回)
```bash
cd ~/clode
bash scripts/04_install_lora_trainer.sh          # sd-scripts を venv-train/ に入れる(PyTorch は使い回し)
cd illust && source .venv/bin/activate
uv pip install -r requirements-lora.txt           # 自動タグ付けの部品
```

### 学習画像を集める
`characters/<名前>/train/` に、そのキャラクターの画像を **15〜30 枚** 置きます。
- 三面図は 1 体ずつ切り出す(正面・横・後ろを別々の画像に)
- 表情、ポーズ、構図(顔のアップ・上半身・全身)がばらけているほどよい
- 生成した画像のうち、よく似ているものを加えてもよい
- 背景は白か透明が扱いやすい(透明は自動で白にします)
- **実在人物の写真、未成年に見える絵、R18 の絵は入れない**(一般向けのデザイン画でよい)

### 学習データを作る(自動タグ付け)
```bash
python -m illustgen lora-prep -c characters/meto
```
- 各画像にタグを付け、`characters/meto/lora/dataset/` に保存します(初回はタグ付け用モデル約 400MB を取得)。
- 髪・目など **キャラ固有の特徴のタグは外し**、代わりにトリガーワード(例: `meto_oc`)を付けます。
  これで「meto_oc」という言葉にキャラの見た目が覚えられます。服装のタグは残すので、服は後から変えられます。
- 未成年・実写と判定されるタグが付いた画像があると止まります。
- 表示されたとおり、`character.yaml` に `trigger: meto_oc` を追加します。

### 学習する
**ComfyUI のターミナルで Ctrl+C を押して ComfyUI を止めてから** 実行します(VRAM を空けるため)。
```bash
python -m illustgen lora-train -c characters/meto
```
- 既定: 10 エポック、合計 約 1600 ステップ。RX 9070 XT で 1〜2 時間程度が目安です。
- 結果は `ComfyUI/models/loras/meto_oc.safetensors`。2 エポックごとの途中経過(`meto_oc-000002` …)も残ります。
- `--dry-run` で、実行するコマンドだけを確認できます。

### 使う
`character.yaml` に追記します:
```yaml
trigger: meto_oc
lora:
  - name: meto_oc.safetensors
    strength: 0.8
```
ComfyUI を起動し直してから、参考画像なしで生成します:
```bash
python -m illustgen generate -c characters/meto -s "standing, smile, cowboy shot" --no-reference
# 途中経過のほうがよい場合もあるので比べる
python -m illustgen generate -c characters/meto -s "standing, smile, cowboy shot" --no-reference --lora meto_oc-000006 --seed 1
```
似すぎて崩れる・絵が固い → 強さを下げる(`--lora meto_oc:0.6`)か、早いエポックを使う。
似ない → 強さを上げる、エポックを増やす(`--epochs 14`)、学習画像を増やす。

## 6. 画像の一部だけ直す(インペイント)

ComfyUI の画面で使うワークフローを用意しています(illustgen の安全チェックは通らないので、
成人の自作キャラクターの画像だけに使ってください)。

1. `workflows/inpaint.json`(LoRA を作ったあとは `inpaint_lora.json`)を ComfyUI の画面にドラッグ&ドロップ
2. 「直したい画像」ノードの **upload** から画像を選ぶ
3. そのノードを右クリック → **マスクエディタで開く** → 直したい部分を塗って **保存**
4. Positive に直したあとの内容のタグを書く(例: `smile, closed mouth` / `detailed hands, five fingers`)
5. 「生成」ノードの **denoise** を調整して実行(0.4〜0.6: 軽く直す、0.7〜1.0: 大きく変える)

塗った部分以外は元の画像のまま残ります。結果は `ComfyUI/output/inpaint_*.png` です。

## ファイル構成

| パス | 内容 |
|---|---|
| `illustgen/cli.py` | コマンド本体(プロンプト組み立て・生成・保存) |
| `illustgen/safety.py` | 安全チェック(年齢・禁止語・写真判定・強制プロンプト) |
| `illustgen/character.py` | `character.yaml` の読み込み |
| `illustgen/lora.py` | LoRA 学習データの準備(自動タグ付け)と学習コマンド |
| `illustgen/providers/comfyui.py` | ComfyUI API クライアント、LoRA の自動挿入 |
| `workflows/sdxl_ipadapter.json` | 参考画像あり(IP-Adapter)のワークフロー |
| `workflows/inpaint.json` / `inpaint_lora.json` | ComfyUI の画面で使うインペイント用(LoRA なし / あり) |
| `workflows/sdxl_txt2img.json` | 参考画像なしのワークフロー |
| `tests/` | `python -m pytest tests` |

ワークフローは ComfyUI の「API 形式で保存」と同じ形式で、`"{{positive}}"` のようなプレースホルダが値に置き換わります。
ComfyUI で作り込んだワークフローを API 形式で書き出し、値をプレースホルダにすれば差し替えられます。
