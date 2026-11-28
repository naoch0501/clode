import io
import re
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
    pngs = list((tmp_path / "out/test").rglob("*.png"))
    assert len(pngs) == 1 and pngs[0].name == "42_0.png"
    run_dir = pngs[0].parent
    assert run_dir.name.endswith("_r18_sitting_bedroom")
    note = (run_dir / "prompt.txt").read_text(encoding="utf-8")
    assert "seed: 42" in note and "--- Positive ---" in note and "nsfw" in note
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


# --- 画風 LoRA -------------------------------------------------------------------
def write_style(d: Path, **over) -> Path:
    data = {"name": "S", "source": "own", "trigger": "my_style", "strength": 0.6}
    data.update(over)
    d.mkdir(parents=True, exist_ok=True)
    (d / "style.yaml").write_text(json.dumps(data), encoding="utf-8")
    return d


def test_style_requires_source(tmp_path):
    lora.check_style(lora.Style.load(write_style(tmp_path / "ok")))
    lora.check_style(lora.Style.load(write_style(tmp_path / "perm", source="permitted")))
    with pytest.raises(safety.SafetyError, match="own"):
        lora.check_style(lora.Style.load(write_style(tmp_path / "bad", source="artist_x")))
    with pytest.raises(safety.SafetyError):
        lora.check_style(lora.Style.load(write_style(tmp_path / "bad2", source="")))


def test_prepare_style_keeps_all_tags(tmp_path):
    d = write_style(tmp_path / "s")
    (d / "train").mkdir()
    png(d / "train" / "a.png")
    out = lora.prepare_style(d, lora.Style.load(d), "my_style",
                             tagger=fake_tagger(["1girl", "red hair", "blue eyes", "smile"]))
    assert (out / "001.txt").read_text() == "my_style, 1girl, red hair, blue eyes, smile"
    with pytest.raises(safety.SafetyError):
        lora.prepare_style(d, lora.Style.load(d), "my_style", tagger=fake_tagger(["photo (medium)"]))


def test_generate_with_style(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), FakeComfy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = __import__("yaml").safe_load((ROOT / "config.example.yaml").read_text())
    cfg["comfyui"]["url"] = f"http://127.0.0.1:{server.server_port}"
    cfg["output_dir"] = str(tmp_path / "out")
    cfg["audit_log"] = str(tmp_path / "out/audit.jsonl")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(json.dumps(cfg))
    char = write_char(tmp_path / "chars/meto", trigger="meto_oc", lora=[{"name": "meto_oc-000008.safetensors"}])
    style = write_style(tmp_path / "styles/pastel")
    try:
        main(["--config", str(cfg_path), "generate", "-c", str(char), "-s", "smile", "--no-reference",
              "--style", str(style)])
    finally:
        server.shutdown()
    wf = FakeComfy.received["prompt"]
    text = wf["6"]["inputs"]["text"]
    assert "meto_oc" in text and "my_style" in text
    assert wf["lora_0"]["inputs"]["lora_name"] == "meto_oc-000008.safetensors"
    assert wf["lora_1"]["inputs"]["lora_name"] == "my_style.safetensors"
    assert wf["lora_1"]["inputs"]["strength_model"] == 0.6


def test_style_only_does_not_add_character_trigger():
    c = Character(name="t", age=25, declared_original=True, trigger="meto_oc",
                  lora=[{"name": "s.safetensors", "trigger": "my_style"}])
    assert c.prompt_fragment().startswith("my_style")
    assert "meto_oc" not in c.prompt_fragment()


def test_lora_train_style_dry_run(tmp_path, capsys):
    d = write_style(tmp_path / "styles/pastel")
    (d / "lora" / "dataset").mkdir(parents=True)
    for i in range(40):
        png(d / "lora" / "dataset" / f"{i:03d}.png")
    base = tmp_path / "base.safetensors"
    base.write_bytes(b"")
    main(["--config", str(ROOT / "config.example.yaml"), "lora-train", "--style", str(d),
          "--base", str(base), "--dry-run"])
    out = capsys.readouterr().out
    assert "40 枚 × 5 回 × 10 エポック = 2000 ステップ" in out
    assert "--output_name my_style" in out


def test_broken_yaml_gives_readable_error(tmp_path):
    d = tmp_path / "chars/broken"
    d.mkdir(parents=True)
    (d / "character.yaml").write_text(
        'name: b\nsource: original\nage: 25\nappearance: "1girl, smile"outfit: "bikini"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="設定ファイルの誤り"):
        main(["--config", str(ROOT / "config.example.yaml"), "check", "-c", str(d)])
    (d / "character.yaml").write_text('name: b\nreference_images:[]\nage: 25\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="2 行目"):
        main(["--config", str(ROOT / "config.example.yaml"), "check", "-c", str(d)])


def test_missing_character_folder(tmp_path):
    with pytest.raises(SystemExit, match="ファイルが見つかりません"):
        main(["--config", str(ROOT / "config.example.yaml"), "check", "-c", str(tmp_path / "nope")])


def test_run_folder_name():
    from illustgen.cli import run_folder_name
    assert run_folder_name("テスト/1", "x", "general").endswith("_テスト-1")
    assert run_folder_name(None, "(from above:1.3), pov, 1boy, fellatio", "r18").endswith("_r18_from above_pov_1boy_fellatio".replace(" ", "-"))
    assert run_folder_name(None, "", "general").endswith("_run")


def test_solo_removed_when_scene_has_other_people():
    from illustgen.cli import drop_solo, has_other_people
    assert has_other_people("(1boy:1.2), hetero, fellatio")
    assert has_other_people("2girls, hug")
    assert not has_other_people("solo, standing, smile")
    assert not has_other_people("1girl, smile")
    assert drop_solo("1girl, solo, (solo:1.2), solo focus, smile") == "1girl, solo focus, smile"


def test_character_default_style_and_cfg(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), FakeComfy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = __import__("yaml").safe_load((ROOT / "config.example.yaml").read_text())
    cfg["comfyui"]["url"] = f"http://127.0.0.1:{server.server_port}"
    cfg["output_dir"] = str(tmp_path / "out")
    cfg["audit_log"] = str(tmp_path / "out/audit.jsonl")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(json.dumps(cfg))
    style = write_style(tmp_path / "styles/pastel")
    char = write_char(tmp_path / "chars/meto", trigger="meto_oc", lora=[{"name": "meto_oc.safetensors", "strength": 0.9}],
                      style=str(style), style_strength=0.4, cfg=4.5, reference_images=[])
    try:
        main(["--config", str(cfg_path), "generate", "-c", str(char), "-s", "smile"])
        wf = FakeComfy.received["prompt"]
        assert wf["3"]["inputs"]["cfg"] == 4.5
        assert wf["lora_1"]["inputs"]["lora_name"] == "my_style.safetensors"
        assert wf["lora_1"]["inputs"]["strength_model"] == 0.4
        main(["--config", str(cfg_path), "generate", "-c", str(char), "-s", "smile", "--style-strength", "0.7", "--cfg", "5"])
        wf = FakeComfy.received["prompt"]
        assert wf["lora_1"]["inputs"]["strength_model"] == 0.7 and wf["3"]["inputs"]["cfg"] == 5.0
        main(["--config", str(cfg_path), "generate", "-c", str(char), "-s", "smile", "--no-style"])
        wf = FakeComfy.received["prompt"]
        assert "lora_1" not in wf
    finally:
        server.shutdown()


# --- duo（2 人） ----------------------------------------------------------------
def _duo_env(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), FakeComfy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = __import__("yaml").safe_load((ROOT / "config.example.yaml").read_text())
    cfg["comfyui"]["url"] = f"http://127.0.0.1:{server.server_port}"
    cfg["output_dir"] = str(tmp_path / "out")
    cfg["audit_log"] = str(tmp_path / "out/audit.jsonl")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(json.dumps(cfg))
    return server, cfg_path


def test_duo_builds_regional_and_per_character_lora(tmp_path):
    server, cfg_path = _duo_env(tmp_path)
    style = write_style(tmp_path / "styles/pastel")
    a = write_char(tmp_path / "chars/meto", name="meto", trigger="meto_oc", appearance="1girl, solo, silver hair",
                   lora=[{"name": "meto_oc.safetensors", "strength": 0.7}], reference_images=[],
                   style=str(style), style_strength=0.4)
    b = write_char(tmp_path / "chars/beni", name="beni", trigger="beni_oc", appearance="1girl, green hair",
                   lora=[{"name": "beni_oc-000008.safetensors", "strength": 0.8}], reference_images=[])
    try:
        main(["--config", str(cfg_path), "duo", "-a", str(a), "-b", str(b), "-s", "standing, smile",
              "--scene-a", "peace sign", "--seed", "10", "--refine", "0.5"])
    finally:
        server.shutdown()
    wf = FakeComfy.received["prompt"]
    for nid, node in wf.items():
        for v in node["inputs"].values():
            if isinstance(v, list):
                assert v[0] in wf, (nid, v)
    by_type = {}
    for node in wf.values():
        by_type.setdefault(node["class_type"], []).append(node)
    loras = sorted(n["inputs"]["lora_name"] for n in by_type["LoraLoader"])
    assert loras == ["beni_oc-000008.safetensors", "meto_oc.safetensors", "my_style.safetensors"]
    areas = sorted(n["inputs"]["x"] for n in by_type["ConditioningSetArea"])
    assert areas == [0, 608]
    seeds = sorted(n["inputs"]["seed"] for n in by_type["KSampler"])
    assert seeds == [10, 11, 12]
    assert sorted(n["inputs"]["denoise"] for n in by_type["KSampler"]) == [0.5, 0.5, 1.0]
    texts = [n["inputs"]["text"] for n in by_type["CLIPTextEncode"]]
    assert any("2girls" in t for t in texts)
    assert any("meto_oc" in t and "silver hair" in t and "peace sign" in t for t in texts)
    assert any("beni_oc" in t and "green hair" in t for t in texts)
    assert not any(re.search(r"(?<![a-z])solo(?! focus)", t) for t in texts)
    assert all("meto_oc" not in t and "beni_oc" not in t for t in texts if "2girls" in t)
    out = list((tmp_path / "out/duo/meto_beni").rglob("*.png"))
    assert len(out) == 1 and (out[0].parent / "prompt.txt").exists()


def test_duo_checks_both_characters(tmp_path):
    a = write_char(tmp_path / "chars/a")
    b = write_char(tmp_path / "chars/b", age=16)
    with pytest.raises(SystemExit, match="age"):
        main(["--config", str(ROOT / "config.example.yaml"), "duo", "-a", str(a), "-b", str(b), "-s", "smile"])
    with pytest.raises(SystemExit, match="安全チェック"):
        main(["--config", str(ROOT / "config.example.yaml"), "duo", "-a", str(a), "-b", str(a), "-s", "smile",
              "--scene-b", "loli"])
