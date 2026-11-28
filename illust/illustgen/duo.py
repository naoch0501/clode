"""2 人を 1 枚に描く（duo）ワークフローを組み立てる。

  1. 構図: キャラ LoRA なしで、左右の領域にそれぞれのキャラの外見タグを効かせて描く
           （ConditioningSetArea による領域別プロンプト）
  2. 左の人をキャラ A の LoRA で描き直す（左半分のマスク + SetLatentNoiseMask）
  3. 右の人をキャラ B の LoRA で描き直す（右半分のマスク）
ComfyUI 標準ノードだけを使う。
"""
from __future__ import annotations


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self._n = 0

    def add(self, class_type: str, title: str | None = None, **inputs) -> str:
        self._n += 1
        nid = str(self._n)
        node = {"class_type": class_type, "inputs": inputs}
        if title:
            node["_meta"] = {"title": title}
        self.nodes[nid] = node
        return nid


def _lora_chain(g: Graph, model, clip, loras: list[dict]):
    for lora in loras:
        s = float(lora.get("strength", 0.8))
        nid = g.add("LoraLoader", lora_name=lora["name"], strength_model=s,
                    strength_clip=float(lora.get("strength_clip", s)), model=model, clip=clip)
        model, clip = [nid, 0], [nid, 1]
    return model, clip


def _half_mask(g: Graph, width: int, height: int, left: bool, overlap: int, feather: int):
    """左半分（または右半分）を 1、それ以外を 0 にしたマスク。境目はぼかす。"""
    half = width // 2 + overlap
    base = g.add("SolidMask", value=0.0, width=width, height=height)
    area = g.add("SolidMask", value=1.0, width=half, height=height)
    x = 0 if left else width - half
    comp = g.add("MaskComposite", destination=[base, 0], source=[area, 0], x=x, y=0, operation="add")
    return [g.add("FeatherMask", mask=[comp, 0], left=0 if left else feather, top=0,
                  right=feather if left else 0, bottom=0), 0]


def _refine(g: Graph, image, vae, model, clip, text: str, negative: str, mask, seed: int, d: dict,
            denoise: float, title: str):
    pos = g.add("CLIPTextEncode", title=f"{title}: Positive", text=text, clip=clip)
    neg = g.add("CLIPTextEncode", text=negative, clip=clip)
    lat = g.add("VAEEncode", pixels=image, vae=vae)
    masked = g.add("SetLatentNoiseMask", samples=[lat, 0], mask=mask)
    ks = g.add("KSampler", title=title, seed=seed, steps=d["steps"], cfg=d["cfg"], sampler_name=d["sampler"],
               scheduler=d["scheduler"], denoise=denoise, model=model, positive=[pos, 0], negative=[neg, 0],
               latent_image=[masked, 0])
    dec = g.add("VAEDecode", samples=[ks, 0], vae=vae)
    comp = g.add("ImageCompositeMasked", destination=image, source=[dec, 0], x=0, y=0,
                 resize_source=False, mask=mask)
    return [comp, 0]


def build(d: dict, seed: int, base_prompt: str, region_a: str, region_b: str, refine_a: str, refine_b: str,
          negative: str, style_loras: list[dict], loras_a: list[dict], loras_b: list[dict],
          denoise: float = 0.55, overlap: int = 64, feather: int = 96, region_strength: float = 1.0) -> dict:
    W, H = int(d["width"]), int(d["height"])
    g = Graph()
    ckpt = g.add("CheckpointLoaderSimple", title="モデル", ckpt_name=d["checkpoint"])
    base_model, base_clip = _lora_chain(g, [ckpt, 0], [ckpt, 1], style_loras)
    vae = [ckpt, 2]

    # 1. 構図（領域別プロンプト）
    glob = g.add("CLIPTextEncode", title="構図: 全体", text=base_prompt, clip=base_clip)
    a = g.add("CLIPTextEncode", title="構図: 左の人", text=region_a, clip=base_clip)
    b = g.add("CLIPTextEncode", title="構図: 右の人", text=region_b, clip=base_clip)
    a_area = g.add("ConditioningSetArea", conditioning=[a, 0], width=W // 2, height=H, x=0, y=0,
                   strength=region_strength)
    b_area = g.add("ConditioningSetArea", conditioning=[b, 0], width=W // 2, height=H, x=W // 2, y=0,
                   strength=region_strength)
    c1 = g.add("ConditioningCombine", conditioning_1=[glob, 0], conditioning_2=[a_area, 0])
    pos = g.add("ConditioningCombine", conditioning_1=[c1, 0], conditioning_2=[b_area, 0])
    neg = g.add("CLIPTextEncode", text=negative, clip=base_clip)
    empty = g.add("EmptyLatentImage", width=W, height=H, batch_size=1)
    ks = g.add("KSampler", title="構図", seed=seed, steps=d["steps"], cfg=d["cfg"], sampler_name=d["sampler"],
               scheduler=d["scheduler"], denoise=1.0, model=base_model, positive=[pos, 0], negative=[neg, 0],
               latent_image=[empty, 0])
    image = [g.add("VAEDecode", samples=[ks, 0], vae=vae), 0]

    # 2, 3. 左右をそれぞれのキャラ LoRA で描き直す
    model_a, clip_a = _lora_chain(g, base_model, base_clip, loras_a)
    model_b, clip_b = _lora_chain(g, base_model, base_clip, loras_b)
    mask_a = _half_mask(g, W, H, True, overlap, feather)
    mask_b = _half_mask(g, W, H, False, overlap, feather)
    image = _refine(g, image, vae, model_a, clip_a, refine_a, negative, mask_a, seed + 1, d, denoise, "左の人")
    image = _refine(g, image, vae, model_b, clip_b, refine_b, negative, mask_b, seed + 2, d, denoise, "右の人")
    g.add("SaveImage", filename_prefix="illustgen_duo", images=image)
    return g.nodes
