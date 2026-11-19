# RunPod で ComfyUI を用意する

> 利用前に RunPod の利用規約・Acceptable Use Policy を確認してください。モデル配布元
> (Civitai / Hugging Face)のライセンスも、成人向け利用が許可されているか確認してください。

## 1. Pod を作る
1. https://www.runpod.io でアカウントを作り、クレジットをチャージする
2. **Pods → Deploy** で GPU を選ぶ(目安: RTX 4090 24GB。SDXL なら 16GB 以上あれば足りる)
3. テンプレートで **ComfyUI** を検索して選ぶ(ComfyUI-Manager 入りのもの)
4. **Volume(Network Volume)を付ける**: モデルを毎回ダウンロードしなくて済む(50GB 程度)
5. ポート 8188 が HTTP で公開されていることを確認して Deploy

起動したら **Connect → HTTP 8188** で ComfyUI の画面が開きます。その URL
(`https://<POD_ID>-8188.proxy.runpod.net`)を `config.yaml` の `comfyui.url` に書きます。

⚠️ この URL を知っている人は誰でも Pod の ComfyUI を操作できます。URL を共有しないでください。
使い終わったら **Stop** してください(停止中もボリューム代は少しかかります)。

## 2. カスタムノードとモデル

ComfyUI 画面の **Manager → Custom Nodes Manager** で `ComfyUI_IPAdapter_plus` をインストールし、再起動します。

Pod の Web Terminal(または Jupyter)で、次のモデルを入れます(パスはテンプレートにより
`/workspace/ComfyUI` や `/ComfyUI` など)。

```bash
cd /workspace/ComfyUI/models
mkdir -p ipadapter clip_vision
# IP-Adapter (SDXL, PLUS)
wget -O ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors \
  https://huggingface.co/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors
# CLIP Vision (ViT-H)
wget -O clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors \
  https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors
```

チェックポイント(SDXL 系のアニメ調モデル。Illustrious XL / NoobAI XL 系など)を
`models/checkpoints/` に置き、ファイル名を `config.yaml` の `defaults.checkpoint` に書きます。
Civitai からダウンロードする場合は API キーが必要なことがあります:
`wget -O checkpoints/model.safetensors "https://civitai.com/api/download/models/<ID>?token=<KEY>"`

キャラ LoRA は `models/loras/` に置きます。

## 3. 動作確認

```bash
python -m illustgen ping
python -m illustgen generate -c characters/example -s "standing, upper body, simple background"
```

`Value not in list: ckpt_name` と出たら、チェックポイントのファイル名が違います。
`IPAdapterUnifiedLoader` が無いと出たら、カスタムノードの再起動を忘れています。
`ClipVision model not found` と出たら、clip_vision のファイル名を上の名前に合わせてください。

## 参考: 他の選択肢
- **ローカル**: RX 9070 XT でも ComfyUI は ROCm で動きます(`comfyui.url` を `http://127.0.0.1:8188` に)。
- **API 型サービス(Replicate など)**: 多くは利用規約で性的コンテンツを禁止しているか、
  セーフティフィルタが外せないため、このツールでは採用していません。
