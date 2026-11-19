# illustgen — キャラクターデザインから成人向けイラストを生成

自作キャラクターのデザイン画(参考画像)を元に、そのキャラクターのイラスト(一般向け / R18)を
外部 GPU 上の **ComfyUI** で生成する CLI です。

```
characters/<名前>/            安全チェック          ComfyUI (RunPod)               outputs/<名前>/
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

## 1. 外部 GPU (RunPod + ComfyUI) を用意する

手順は [docs/runpod-setup.md](docs/runpod-setup.md) にあります。概要は次のとおりです。

1. RunPod で ComfyUI テンプレートの Pod を起動する(VRAM 16GB 以上。RTX 4090 / A5000 など)
2. カスタムノード **ComfyUI_IPAdapter_plus** と、IP-Adapter・CLIP Vision のモデルを入れる
3. SDXL 系のアニメ調チェックポイント(Illustrious XL 系など)を `models/checkpoints/` に置く
4. Pod の URL `https://<POD_ID>-8188.proxy.runpod.net` を `config.yaml` に書く

自分の PC の ComfyUI を使う場合は、URL を `http://127.0.0.1:8188` にするだけで動きます。

## 2. セットアップ

```bash
cd illust
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
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
| `--no-reference` | 参考画像を使わない(LoRA だけで描く) |
| `--width --height --steps --cfg --checkpoint` | 設定ファイルの既定値を上書き |

## ファイル構成

| パス | 内容 |
|---|---|
| `illustgen/cli.py` | コマンド本体(プロンプト組み立て・生成・保存) |
| `illustgen/safety.py` | 安全チェック(年齢・禁止語・写真判定・強制プロンプト) |
| `illustgen/character.py` | `character.yaml` の読み込み |
| `illustgen/providers/comfyui.py` | ComfyUI API クライアント、LoRA の自動挿入 |
| `workflows/sdxl_ipadapter.json` | 参考画像あり(IP-Adapter)のワークフロー |
| `workflows/sdxl_txt2img.json` | 参考画像なしのワークフロー |
| `tests/` | `python -m pytest tests` |

ワークフローは ComfyUI の「API 形式で保存」と同じ形式で、`"{{positive}}"` のようなプレースホルダが値に置き換わります。
ComfyUI で作り込んだワークフローを API 形式で書き出し、値をプレースホルダにすれば差し替えられます。
