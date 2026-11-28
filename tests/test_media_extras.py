"""TTS fallback chain, sound design, internet images + vision check, credits, outro."""
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio.core.config import Settings
from studio.audio import tts
from studio.audio import mix as sound
from studio.research import vision
from studio.editorial.storyboard import validate_storyboard, with_outro
from studio.research import images as im

FIX = Path(__file__).parent / "fixtures"
ROOT_DIR = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ TTS chain
def test_chain_falls_back_and_uses_one_voice(tmp_path, store, monkeypatch):
    s = Settings(dry_run=True, tts_provider="google", tts_fallbacks=("edge", "tone"))
    monkeypatch.setattr(tts.GoogleTTS, "synthesize",
                        lambda self, t, o: (_ for _ in ()).throw(RuntimeError("HTTP 403")))
    clips, name, reasons = tts.synthesize_all(["one two three", "four five"], tmp_path, s, store, log=lambda m: None)
    assert name == "tone" and len(clips) == 2 and all(c.name.startswith("tone_") for c in clips)
    assert reasons[0].startswith("google") and "ALLOW_EDGE_TTS" in reasons[1]


def test_budget_exhaustion_falls_back_before_any_call(tmp_path, store, monkeypatch):
    s = Settings(dry_run=True, tts_provider="google", tts_fallbacks=("tone",), tts_monthly_char_budget=5)
    called = []
    monkeypatch.setattr(tts.GoogleTTS, "synthesize", lambda self, t, o: called.append(t))
    _, name, reasons = tts.synthesize_all(["this is longer than five"], tmp_path, s, store, log=lambda m: None)
    assert name == "tone" and not called and "budget" in reasons[0]


def test_all_providers_failing_raises(tmp_path, store):
    s = Settings(dry_run=True, tts_provider="edge", tts_fallbacks=("piper",))
    with pytest.raises(RuntimeError, match="all TTS providers failed"):
        tts.synthesize_all(["x"], tmp_path, s, store, log=lambda m: None)


def test_tone_refused_outside_dry_run(store):
    with pytest.raises(tts.ProviderUnavailable):
        tts.build_provider("tone", Settings(dry_run=False), store)


@pytest.mark.slow
def test_kokoro_real_synthesis_if_model_present(tmp_path):
    try:
        k = tts.KokoroTTS("am_michael")
    except tts.ProviderUnavailable:
        pytest.skip("Kokoro model not downloaded (scripts/fetch_kokoro.sh)")
    out = k.synthesize("Hash tables find data fast.", tmp_path / "k.wav")
    assert len(sound.read_audio(out)) > 48000 * 0.8


# ------------------------------------------------------------------ sound
def test_procedural_sfx_and_music_are_bounded():
    for kind in sound.SFX_CATEGORIES:
        x = sound.synth_sfx(kind)
        assert 0 < len(x) < 2 * sound.SR and np.abs(x).max() <= 1.0
    m = sound.synth_music(5.0, seed=3)
    assert len(m) == 5 * sound.SR and np.abs(m).max() <= 0.81
    assert not np.array_equal(m, sound.synth_music(5.0, seed=4))


def test_library_ignores_unlicensed_entries(tmp_path):
    sound.write_wav(tmp_path / "a.wav", sound.synth_sfx("pop"))
    sound.write_wav(tmp_path / "b.wav", sound.synth_sfx("tick"))
    (tmp_path / "manifest.json").write_text(json.dumps({"assets": [
        {"file": "a.wav", "category": "pop", "source": "Earshot SFX", "license": "see LICENSE"},
        {"file": "b.wav", "category": "tick", "source": "", "license": ""},
        {"file": "missing.wav", "category": "pop", "source": "x", "license": "y"}]}))
    lib = sound.Library.load(tmp_path)
    assert [a.path.name for a in lib.assets] == ["a.wav"] and len(lib.rejected) == 2


def test_cues_follow_scenes():
    scenes = [{"visual": {"type": "title"}}, {"visual": {"type": "stat"}, "transition": "slide"},
              {"visual": {"type": "question"}, "transition": "cut"}]
    timings = [{"start": 0}, {"start": 5}, {"start": 10}]
    kinds = [k for _, k in sound.plan_cues(scenes, timings)]
    assert kinds == ["impact", "whoosh", "pop", "chime"]


# ------------------------------------------------------------------ images
@pytest.mark.parametrize("lic,ok", [("CC0", True), ("Public domain", True), ("CC BY 4.0", True),
                                    ("CC BY-SA 4.0", False), ("CC BY-NC 2.0", False),
                                    ("CC BY-ND 3.0", False), ("Fair use", False), ("", False)])
def test_license_allowlist(lic, ok):
    assert im.license_allowed(lic) is ok


def png_bytes(color=(10, 120, 200), size=(1200, 900)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


class FakeResp:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        yield self.data


class FakeSession:
    def get(self, url, **kw):
        return FakeResp(png_bytes())


class FakeDescriber:
    def __init__(self, answers):
        self.answers = answers

    def describe(self, p, query=""):
        return self.answers.pop(0)


def cand(title, url, lic="CC BY 4.0"):
    return im.Candidate("Wikimedia Commons", title, url, url + "/page", lic, "Ada", 1600, 1200, "image/png")


def test_choose_rejects_watermark_and_prefers_relevant_caption(tmp_path):
    cands = [cand("Data center servers", "https://x/1.png"), cand("Server room", "https://x/2.png")]
    d = FakeDescriber([{"caption": "racks of servers", "watermark": True},
                       {"caption": "rows of server racks in a data center", "watermark": False,
                        "logo": False, "face": False}])
    best, notes = im.choose("data center servers", im.filter_candidates(cands), tmp_path, "scene3",
                            FakeSession(), d)
    assert best.url.endswith("2.png") and (tmp_path / "assets" / best.file).exists()
    assert any("rejected by vision" in n for n in notes)
    assert "via Wikimedia Commons" in best.credit and best.needs_attribution


def test_filter_drops_small_and_bad_license():
    small = cand("x", "https://x/s.png")
    small.width, small.height = 640, 480
    assert im.filter_candidates([small, cand("y", "https://x/y.png", "CC BY-SA 3.0")]) == []


def test_vision_output_parsing():
    r = vision.parse("CAPTION: A laptop on a desk. TEXT: no. WATERMARK: no. LOGO: yes. FACE: no.")
    assert r["caption"] == "A laptop on a desk" and r["logo"] and not r["watermark"] and r["parsed"]
    assert vision.parse("a cat")["parsed"] is False


def test_vision_command_has_no_shell_injection(tmp_path):
    img = tmp_path / "a b;rm -rf.png"
    img.write_bytes(png_bytes())
    d = vision.CommandDescriber("echo {image} CAPTION: ok. TEXT: no. WATERMARK: no. LOGO: no. FACE: no.")
    out = d.describe(img)
    assert out["caption"].endswith("ok") and img.exists()


class Boom:
    name = "boom"

    def describe(self, p, q=""):
        raise RuntimeError("model crashed")


class Good:
    name = "good"

    def describe(self, p, q=""):
        return {"caption": "racks of servers", "watermark": False, "logo": False, "face": False}


def test_vision_chain_falls_back(tmp_path):
    out = vision.ChainDescriber([Boom(), Good()]).describe(tmp_path / "x.png", "servers")
    assert out["provider"] == "good" and "boom: model crashed" in out["fallback_reasons"][0]
    none = vision.ChainDescriber([Boom()]).describe(tmp_path / "x.png")
    assert none["visual_unverified"] and none["provider"] == "none"


def test_chain_skips_unavailable_providers(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDERS", "omnivision,google,metadata")
    monkeypatch.delenv("VISION_CMD", raising=False)
    monkeypatch.delenv("GOOGLE_VISION_ENABLED", raising=False)
    assert vision.make_describer().name == "metadata"


def test_summary_is_tiny_and_keeps_flags():
    long = {"caption": "The image depicts a server room. A black server rack with many servers and cables. "
                       "A yellow cable connects to the server rack.", "logo": True, "face": True,
            "provider": "smolvlm", "relevance": 0.912}
    s = vision.summarize(long)
    assert s["kw"][:2] == ["server", "rack"] and s["flags"] == "lf" and s["rel"] == 0.91
    assert len(json.dumps(s)) < 130


def test_owner_approval_mode_allows_credited_restricted_images(monkeypatch):
    assert im.license_allowed("CC BY-SA 4.0", "owner_approval")
    assert im.license_allowed("All rights reserved", "owner_approval")
    assert not im.license_allowed("Fair use", "owner_approval")
    monkeypatch.setattr(im, "LICENSE_MODE", "owner_approval")
    named = cand("x", "https://x/a.png", "All rights reserved")
    anon = cand("y", "https://x/b.png", "All rights reserved")
    anon.creator = ""
    kept = im.filter_candidates([named, anon])
    assert kept == [named] and named.requires_permission


# ------------------------------------------------------------------ outro
def test_outro_appended_and_not_in_script():
    sb = json.loads((FIX / "sample_storyboard.json").read_text())
    full = with_outro(sb, "Subscribe for more.")
    assert full["scenes"][-1]["visual"]["type"] == "subscribe" and len(full["scenes"]) == len(sb["scenes"]) + 1
    sb["scenes"][2]["narration"] += " Subscribe now!"
    _, warns = validate_storyboard(sb)
    assert any("added automatically" in w for w in warns)


# ------------------------------------------------------------------ Drive keys.json
from studio.storage import keys as keysmod  # noqa: E402


def test_keys_whitelist_and_never_flags():
    vals, ignored = keysmod.parse(json.dumps({"GOOGLE_TTS_API_KEY": "k", "UPLOAD_ENABLED": "true",
                                              "WEBHOOK_SECRET": "s", "DRIVE_FOLDER_IDS": {"root": "x"},
                                              "APPROVER_EMAIL": "", "_readme": "x"}))
    assert vals == {"GOOGLE_TTS_API_KEY": "k", "DRIVE_FOLDER_IDS": '{"root": "x"}'}
    assert set(ignored) == {"UPLOAD_ENABLED", "WEBHOOK_SECRET"}


@pytest.mark.parametrize("meta", [{"ownedByMe": False}, {"ownedByMe": True, "permissions": [{"type": "anyone"}]},
                                  {"ownedByMe": True, "permissions": [{"type": "domain"}]}])
def test_keys_file_must_be_private(meta):
    with pytest.raises(keysmod.KeysFileRejected):
        keysmod.check_private(meta)


class FakeFiles:
    def __init__(self, meta, raw):
        self.meta, self.raw = meta, raw

    def get(self, **kw):
        return type("R", (), {"execute": lambda s: self.meta})()

    def get_media(self, **kw):
        return type("R", (), {"execute": lambda s: self.raw})()


class FakeDriveSvc:
    def __init__(self, meta, raw):
        self._f = FakeFiles(meta, raw)

    def files(self):
        return self._f


def test_keys_loaded_into_env_without_overriding():
    env = {"YTA_KEYS_FILE_ID": "id1", "TTS_VOICE": "mine"}
    svc = FakeDriveSvc({"ownedByMe": True, "permissions": [{"type": "user"}]},
                       json.dumps({"GOOGLE_TTS_API_KEY": "secret-value", "TTS_VOICE": "theirs"}).encode())
    rep = keysmod.load_into_env(svc, env)
    assert env["GOOGLE_TTS_API_KEY"] == "secret-value" and env["TTS_VOICE"] == "mine"
    assert rep["loaded"] == ["GOOGLE_TTS_API_KEY"] and "secret-value" not in json.dumps(rep)


def test_bootstrap_keys_merge_keeps_owner_values():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bootstrap", ROOT_DIR / "scripts" / "bootstrap.py")
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)
    ids = {"archive": "A1", "root": "R"}
    m = bs.merge_keys({"GOOGLE_TTS_API_KEY": "k", "DRY_RUN": "", "SPREADSHEET_ID": "old"}, ids, "S2", " Me@Example.com ", "SCRIPT")
    assert m["GOOGLE_TTS_API_KEY"] == "k" and m["DRY_RUN"] == "true" and m["SPREADSHEET_ID"] == "S2"
    assert m["ARCHIVE_FOLDER_ID"] == "A1" and m["APPROVER_EMAIL"] == "me@example.com" and m["_apps_script_id"] == "SCRIPT"
    assert bs.merge_keys({"DRY_RUN": "false"}, ids, "S", "")["DRY_RUN"] == "false"


def test_publisher_never_takes_flags_or_secrets_from_drive():
    env = {"YTA_KEYS_FILE_ID": "id1"}
    svc = FakeDriveSvc({"ownedByMe": True, "permissions": []},
                       json.dumps({"DRY_RUN": "false", "SPREADSHEET_ID": "S", "GOOGLE_TTS_API_KEY": "k"}).encode())
    keysmod.load_into_env(svc, env, exclude={"DRY_RUN", "GOOGLE_TTS_API_KEY"})
    assert env.get("SPREADSHEET_ID") == "S" and "DRY_RUN" not in env and "GOOGLE_TTS_API_KEY" not in env


def test_script_admin_falls_back_to_pipeline_login_only_with_scope(monkeypatch):
    from studio.core import google_auth as ga
    monkeypatch.delenv("YTA_SCRIPT_OAUTH", raising=False)
    monkeypatch.setenv("YTA_PIPELINE_OAUTH", json.dumps({"scopes": ["https://www.googleapis.com/auth/drive"]}))
    with pytest.raises(ga.MissingCredentials):
        ga.credentials("script_admin")
