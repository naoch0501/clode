"""ComfyUI (RunPod / ローカル) 向けプロバイダ。

ComfyUI の HTTP API:
  POST /upload/image   参考画像のアップロード
  POST /prompt         ワークフロー投入 → prompt_id
  GET  /history/{id}   完了確認
  GET  /view           生成画像の取得
"""
from __future__ import annotations

import copy
import io
import json
import time
import uuid
from pathlib import Path

import requests
from PIL import Image


def flatten_to_white(path: Path) -> bytes:
    """透明部分を白で埋めた PNG を返す（ComfyUI の LoadImage は透明を無視して読むため）。"""
    with Image.open(path) as img:
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            out = Image.new("RGB", rgba.size, (255, 255, 255))
            out.paste(rgba, mask=rgba.split()[3])
        else:
            out = img.convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def _fill(obj, values: dict):
    """"{{key}}" プレースホルダを values で置換する（文字列全体が一致すれば型ごと置換）。"""
    if isinstance(obj, dict):
        return {k: _fill(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, values) for v in obj]
    if isinstance(obj, str) and obj.startswith("{{") and obj.endswith("}}"):
        key = obj[2:-2].strip()
        if key not in values:
            raise KeyError(f"ワークフローのプレースホルダ {obj} に対応する値がありません")
        return values[key]
    return obj


class ComfyUIProvider:
    def __init__(self, url: str, api_key: str | None = None, timeout: int = 600, **_):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"
        self.client_id = str(uuid.uuid4())

    def ping(self) -> dict:
        r = self.session.get(f"{self.url}/system_stats", timeout=30)
        r.raise_for_status()
        return r.json()

    def upload(self, path: Path) -> str:
        r = self.session.post(
            f"{self.url}/upload/image",
            files={"image": (f"{path.stem}.png", flatten_to_white(path), "image/png")},
            data={"overwrite": "true"},
            timeout=120,
        )
        r.raise_for_status()
        info = r.json()
        return f"{info['subfolder']}/{info['name']}" if info.get("subfolder") else info["name"]

    def generate(self, workflow_path: Path, values: dict, refs: list[Path], loras: list[dict] | None = None) -> list[bytes]:
        workflow = apply_loras(json.loads(workflow_path.read_text(encoding="utf-8")), loras or [])
        values = dict(values)
        if refs:
            values["reference_image"] = self.upload(refs[0])
        wf = _fill(copy.deepcopy(workflow), values)
        return self.run(wf)

    def run(self, wf: dict) -> list[bytes]:
        """API 形式のワークフローをそのまま実行して、出力画像を返す。"""
        r = self.session.post(f"{self.url}/prompt", json={"prompt": wf, "client_id": self.client_id}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"ComfyUI がワークフローを拒否しました: {r.status_code} {r.text[:1000]}")
        prompt_id = r.json()["prompt_id"]

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            h = self.session.get(f"{self.url}/history/{prompt_id}", timeout=30)
            h.raise_for_status()
            entry = h.json().get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(f"生成エラー: {json.dumps(status.get('messages'), ensure_ascii=False)[:1000]}")
                if status.get("completed", True):
                    return self._collect(entry)
            time.sleep(2)
        raise TimeoutError(f"{self.timeout} 秒以内に生成が終わりませんでした")

    def _collect(self, entry: dict) -> list[bytes]:
        images = []
        for node_out in entry.get("outputs", {}).values():
            for img in node_out.get("images", []):
                if img.get("type") != "output":
                    continue
                r = self.session.get(f"{self.url}/view", params=img, timeout=120)
                r.raise_for_status()
                images.append(r.content)
        return images


def apply_loras(workflow: dict, loras: list[dict]) -> dict:
    """CheckpointLoaderSimple の直後に LoraLoader を連結し、参照を付け替える。"""
    if not loras:
        return workflow
    ckpt_id = next(k for k, v in workflow.items() if v.get("class_type") == "CheckpointLoaderSimple")
    prev_model, prev_clip = [ckpt_id, 0], [ckpt_id, 1]
    new_nodes = {}
    for i, lora in enumerate(loras):
        nid = f"lora_{i}"
        new_nodes[nid] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": lora["name"],
                "strength_model": float(lora.get("strength", 0.8)),
                "strength_clip": float(lora.get("strength_clip", lora.get("strength", 0.8))),
                "model": prev_model,
                "clip": prev_clip,
            },
        }
        prev_model, prev_clip = [nid, 0], [nid, 1]
    for node in workflow.values():
        for key, val in node["inputs"].items():
            if val == [ckpt_id, 0]:
                node["inputs"][key] = prev_model
            elif val == [ckpt_id, 1]:
                node["inputs"][key] = prev_clip
    workflow.update(new_nodes)
    return workflow
