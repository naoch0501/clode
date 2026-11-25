import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

from illustgen import safety
from illustgen.character import Character
from illustgen.cli import main
from illustgen.providers.comfyui import _fill, apply_loras

ROOT = Path(__file__).resolve().parent.parent


def png(path: Path, exif_camera: bool = False) -> Path:
    img = Image.new("RGB", (64, 64), "white")
    kw = {}
    if exif_camera:
        ex = Image.Exif()
        ex[0x010F] = "Canon"
        ex[0x0110] = "EOS R5"
        kw["exif"] = ex
    img.save(path, "JPEG" if exif_camera else "PNG", **kw)
    return path


def write_char(d: Path, **over) -> Path:
    data = {"name": "Test", "source": "original", "age": 25,
            "appearance": "1girl, adult woman, red hair", "reference_images": ["ref.png"]}
    data.update(over)
    d.mkdir(parents=True, exist_ok=True)
    (d / "character.yaml").write_text(json.dumps(data), encoding="utf-8")
    if not (d / "ref.png").exists():
        png(d / "ref.png")
    return d


@pytest.mark.parametrize("text", ["loli, bed", "1girl, School Girl", "1girl, schoolgirl", "女子高生", "ロリ",
                                  "teen, smile", "photorealistic, woman", "celebrity", "実写"])
def test_blocked_prompts(text):
    with pytest.raises(safety.SafetyError):
        safety.check_prompt(text.replace("School Girl", "schoolgirl"))


@pytest.mark.parametrize("text", ["1girl, adult woman, office, sitting", "mature female, bedroom, night",
                                  "kidney-shaped pool", "skidding", "成人女性、夜景"])
def test_allowed_prompts(text):
    safety.check_prompt(text)


def test_character_age_and_source(tmp_path):
    safety.check_character(Character.load(write_char(tmp_path / "ok")))
    with pytest.raises(safety.SafetyError, match="age"):
        safety.check_character(Character.load(write_char(tmp_path / "young", age=17)))
    with pytest.raises(safety.SafetyError, match="age"):
        safety.check_character(Character.load(write_char(tmp_path / "noage", age="adult")))
    with pytest.raises(safety.SafetyError, match="original"):
        safety.check_character(Character.load(write_char(tmp_path / "src", source="fanart")))
    with pytest.raises(safety.SafetyError):
        safety.check_character(Character.load(write_char(tmp_path / "desc", appearance="loli")))


def test_photo_reference_rejected(tmp_path):
    safety.check_reference_image(png(tmp_path / "design.png"))
    with pytest.raises(safety.SafetyError, match="EXIF"):
        safety.check_reference_image(png(tmp_path / "photo.jpg", exif_camera=True))


def test_fill_and_loras():
    wf = json.loads((ROOT / "workflows/sdxl_ipadapter.json").read_text())
    wf = apply_loras(wf, [{"name": "a.safetensors"}, {"name": "b.safetensors", "strength": 0.5}])
    assert wf["10"]["inputs"]["model"] == ["lora_1", 0]
    assert wf["6"]["inputs"]["clip"] == ["lora_1", 1]
    assert wf["lora_1"]["inputs"]["model"] == ["lora_0", 0]
    assert wf["lora_0"]["inputs"]["model"] == ["4", 0]
    assert wf["8"]["inputs"]["vae"] == ["4", 2]
    filled = _fill(wf, {k: 1 for k in ["checkpoint", "positive", "negative", "width", "height", "batch_size",
                                       "seed", "steps", "cfg", "sampler", "scheduler", "ipadapter_preset",
                                       "reference_image", "reference_weight", "reference_start_at", "reference_end_at"]})
    assert "{{" not in json.dumps(filled)
    with pytest.raises(KeyError):
        _fill(wf, {})


class FakeComfy(BaseHTTPRequestHandler):
    received = {}

    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/upload/image":
            self._json({"name": "ref.png", "subfolder": "", "type": "input"})
        elif self.path == "/prompt":
            FakeComfy.received["prompt"] = json.loads(data)["prompt"]
            self._json({"prompt_id": "abc"})

    def do_GET(self):
        if self.path.startswith("/history/abc"):
            self._json({"abc": {"status": {"status_str": "success", "completed": True},
                                "outputs": {"9": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}}}})
        elif self.path.startswith("/view"):
            buf = io.BytesIO()
            Image.new("RGB", (8, 8)).save(buf, "PNG")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(buf.getvalue())


def test_generate_end_to_end(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), FakeComfy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = json.loads(json.dumps(__import__("yaml").safe_load((ROOT / "config.example.yaml").read_text())))
    cfg["comfyui"]["url"] = f"http://127.0.0.1:{server.server_port}"
    cfg["output_dir"] = str(tmp_path / "out")
    cfg["audit_log"] = str(tmp_path / "out/audit.jsonl")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(json.dumps(cfg))
    char = write_char(tmp_path / "chars/test", lora=[{"name": "c.safetensors"}])
    try:
        main(["--config", str(cfg_path), "generate", "-c", str(char), "-s", "sitting, bedroom",
              "-r", "r18", "--seed", "42", "--start-at", "0.3", "--no-outfit"])
    finally:
        server.shutdown()
    wf = FakeComfy.received["prompt"]
    assert wf["3"]["inputs"]["seed"] == 42
    assert "nsfw" in wf["6"]["inputs"]["text"] and "adult" in wf["6"]["inputs"]["text"]
    assert "loli" in wf["7"]["inputs"]["text"]
    assert "red hair" in wf["6"]["inputs"]["text"]
    assert wf["11"]["inputs"]["image"] == "ref.png"
    assert wf["12"]["inputs"]["start_at"] == 0.3 and wf["12"]["inputs"]["end_at"] == 0.8
    assert "lora_0" in wf
    assert len(list((tmp_path / "out/test").glob("*.png"))) == 1
    log = json.loads((tmp_path / "out/audit.jsonl").read_text().splitlines()[0])
    assert log["seed"] == 42 and log["references"][0]["sha256"]


def test_generate_blocked_scene(tmp_path):
    char = write_char(tmp_path / "chars/test")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text((ROOT / "config.example.yaml").read_text())
    with pytest.raises(SystemExit, match="安全チェック"):
        main(["--config", str(cfg_path), "generate", "-c", str(char), "-s", "school uniform, loli"])


def test_flatten_to_white(tmp_path):
    from illustgen.providers.comfyui import flatten_to_white
    p = tmp_path / "t.png"
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0, 255))
    img.save(p)
    out = Image.open(io.BytesIO(flatten_to_white(p)))
    assert out.mode == "RGB"
    assert out.getpixel((1, 1)) == (255, 255, 255)
    assert out.getpixel((0, 0)) == (255, 0, 0)


# --- LoRA ---------------------------------------------------------------------
from illustgen import lora  # noqa: E402


def fake_tagger(tags):
    return lambda img: list(tags)


def test_build_caption_prunes_intrinsic_tags():
    char = Character(name="t", age=25, declared_original=True, appearance="1girl, silver hair, purple eyes, slim waist")
    cap = lora.build_caption("meto_oc", ["1girl", "solo", "silver hair", "short hair", "purple eyes", "messy bangs",
                                         "slim waist", "smile", "black shorts", "white background"], char)
    assert cap == "meto_oc, 1girl, solo, smile, black shorts, white background"


def test_prepare_dataset(tmp_path):
    d = write_char(tmp_path / "chars/meto")
    (d / "train").mkdir()
    png(d / "train" / "a.png")
    png(d / "train" / "b.png")
    out = lora.prepare(d, Character.load(d), "meto_oc", tagger=fake_tagger(["1girl", "red hair", "smile"]))
    assert sorted(p.name for p in out.iterdir()) == ["001.png", "001.txt", "002.png", "002.txt"]
    assert (out / "001.txt").read_text() == "meto_oc, 1girl, smile"


def test_prepare_rejects_minor_or_photo_tags(tmp_path):
    d = write_char(tmp_path / "chars/meto")
    (d / "train").mkdir()
    png(d / "train" / "a.png")
    with pytest.raises(safety.SafetyError, match="a.png"):
        lora.prepare(d, Character.load(d), "meto_oc", tagger=fake_tagger(["1girl", "loli"]))
    with pytest.raises(safety.SafetyError, match="realistic"):
        lora.prepare(d, Character.load(d), "meto_oc", tagger=fake_tagger(["1girl", "realistic"]))
    assert not list((d / "lora" / "dataset").iterdir())


def test_prepare_requires_images(tmp_path):
    d = write_char(tmp_path / "chars/meto")
    with pytest.raises(safety.SafetyError, match="train"):
        lora.prepare(d, Character.load(d), "meto_oc", tagger=fake_tagger([]))


def test_toml_and_command(tmp_path):
    t = lora.dataset_toml(tmp_path / "ds", 8)
    assert "num_repeats = 8" in t and "shuffle_caption = false" in t
    assert lora.auto_repeats(20, 10) == 8
    cmd = lora.train_command(tmp_path, tmp_path / "m.safetensors", tmp_path / "d.toml", tmp_path / "out",
                             "meto_oc", 10, 16, 1e-4, tmp_path / "logs")
    assert "--network_train_unet_only" in cmd and "--cache_text_encoder_outputs" in cmd
    assert cmd[cmd.index("--network_alpha") + 1] == "8"
    assert lora.default_trigger(Path("characters/Meto-2")) == "meto2_oc"


def test_trigger_and_lora_override_in_generate(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), FakeComfy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = __import__("yaml").safe_load((ROOT / "config.example.yaml").read_text())
    cfg["comfyui"]["url"] = f"http://127.0.0.1:{server.server_port}"
    cfg["output_dir"] = str(tmp_path / "out")
    cfg["audit_log"] = str(tmp_path / "out/audit.jsonl")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(json.dumps(cfg))
    char = write_char(tmp_path / "chars/meto", trigger="meto_oc")
    try:
        main(["--config", str(cfg_path), "generate", "-c", str(char), "-s", "smile", "--no-reference",
              "--lora", "meto_oc-000006:0.9"])
    finally:
        server.shutdown()
    wf = FakeComfy.received["prompt"]
    assert "meto_oc" in wf["6"]["inputs"]["text"]
    assert wf["lora_0"]["inputs"]["lora_name"] == "meto_oc-000006.safetensors"
    assert wf["lora_0"]["inputs"]["strength_model"] == 0.9
    assert "11" not in wf  # 参考画像なし → txt2img
