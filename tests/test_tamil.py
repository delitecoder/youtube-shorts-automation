"""Tamil versions: ids, voice routing, localize command, discover's localize action."""
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from ytauto.config import Settings
from ytauto.integrations.tts import for_language
from ytauto.production import storyboard as sbm
from ytauto.state import State
from ytauto.store import is_long, language_of, next_video_id

from test_cli_and_service import FIX, run

NOW = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)


def test_id_ranges_per_language_and_format():
    ids = ["VIDEO-20261125-001", "VIDEO-20261125-101"]
    assert next_video_id(ids, NOW) == "VIDEO-20261125-002"
    assert next_video_id(ids, NOW, lang="ta") == "VIDEO-20261125-051"
    assert next_video_id(ids, NOW, long=True) == "VIDEO-20261125-102"
    assert next_video_id(ids, NOW, long=True, lang="ta") == "VIDEO-20261125-151"
    assert next_video_id(ids + ["VIDEO-20261125-051"], NOW) == "VIDEO-20261125-002"
    assert [language_of(v) for v in ("VIDEO-20261125-001", "VIDEO-20261125-051", "VIDEO-20261125-151")] == ["en", "ta", "ta"]
    assert is_long("VIDEO-20261125-151") and not is_long("VIDEO-20261125-051")


def test_tamil_voice_never_falls_back_to_english_engines():
    s = for_language(Settings.from_env({}), "ta")
    assert s.tts_voice == "ta-IN-Chirp3-HD-Charon" and s.tts_language == "ta-IN"
    assert s.tts_fallbacks == ("google_wavenet",) and s.tts_provider == "google"
    assert for_language(Settings.from_env({}), "en").tts_voice.startswith("en-US")


def test_tamil_outro_and_char_cap():
    sb = {"scenes": [{"narration": "x"}], "language": "ta"}
    assert sbm.with_outro(sb, "t")["scenes"][-1]["on_screen_text"] == "பிடித்திருந்ததா?"
    e = []
    sbm._char_check({"language": "ta", "scenes": [{"narration": "அ" * 1400}]}, e, "short")
    assert e == []
    sbm._char_check({"language": "en", "scenes": [{"narration": "a" * 1400}]}, e, "short")
    assert e


def test_localize_creates_tamil_item_once(tmp_path):
    rt = tmp_path / "rt"
    vid = run(rt, "new-item")["video_id"]
    (rt / "work" / vid / "assets").mkdir(parents=True)
    (rt / "work" / vid / "assets" / "a.png").write_bytes(b"x")
    sb = json.loads((FIX / "sample_storyboard.json").read_text()) | {"video_id": vid}
    (tmp_path / "ta.json").write_text(json.dumps(sb, ensure_ascii=False))
    r = run(rt, "localize", "--video-id", vid, "--storyboard", str(tmp_path / "ta.json"))
    assert r["ok"] and r["video_id"].endswith("-051") and r["language"] == "ta"
    out = json.loads(Path(r["storyboard"]).read_text())
    assert out["language"] == "ta" and out["video_id"] == r["video_id"]
    assert (rt / "work" / r["video_id"] / "assets" / "a.png").exists()
    again = run(rt, "localize", "--video-id", vid, "--storyboard", str(tmp_path / "ta.json"))
    assert again["video_id"] == r["video_id"]


def test_discover_asks_for_tamil_version_after_english_submit():
    from ytauto.orchestration import cli
    today = "20261125"
    en = SimpleNamespace(video_id=f"VIDEO-{today}-001", state=State.AWAITING_APPROVAL, source_type="GENERATED", notes="")
    ctx = SimpleNamespace(s=Settings.from_env({}))
    loc = cli._needs_localize(ctx, [en], today)
    assert loc["action"] == "localize" and loc["video_id"] == en.video_id
    ta = SimpleNamespace(video_id=f"VIDEO-{today}-051", state=State.RESEARCHING, source_type="GENERATED",
                         notes=f"ta version of {en.video_id}")
    assert cli._needs_localize(ctx, [en, ta], today) is None
    assert cli._needs_localize(SimpleNamespace(s=Settings.from_env({"VIDEO_LANGUAGES": "en"})), [en], today) is None


def test_tamil_duration_estimate_uses_characters():
    sb = json.loads((FIX / "sample_storyboard.json").read_text())
    ta = dict(sb, language="ta", scenes=[dict(x, narration="அ" * 135) for x in sb["scenes"]])
    est = sbm.estimate(ta)
    assert abs(est["est_seconds"] - len(ta["scenes"]) * (10 + sbm.SCENE_PAD_S)) < 0.5


def test_texts_export_merge_changes_only_text():
    from ytauto.production import texts as tx
    sb = sbm.normalize(json.loads((FIX / "sample_storyboard.json").read_text()))
    sb["scenes"][0]["visual"] = {"type": "custom", "code": "scene1", "full_frame": True}
    items = tx.export(sb)
    assert any(x["path"] == "scenes.0.narration" for x in items)
    assert not any(".visual.code" in x["path"] or "nodes" in x["path"] for x in items)
    ta = [dict(x, ta="த " + x["text"]) for x in items]
    out, errs = tx.merge(sb, ta, "ta")
    assert errs == [] and out["language"] == "ta"
    assert out["scenes"][0]["narration"].startswith("த ")
    for a, b in zip(sb["scenes"], out["scenes"]):          # visuals identical except translated text
        assert a["visual"]["type"] == b["visual"]["type"]
        if a["visual"]["type"] in ("diagram", "code", "custom"):
            assert a["visual"] == b["visual"]
    _, errs = tx.merge(sb, ta[1:], "ta")
    assert errs and "missing translation" in errs[0]


def test_localize_from_texts(tmp_path):
    from ytauto.production import texts as tx
    rt = tmp_path / "rt"
    vid = run(rt, "new-item")["video_id"]
    sb = json.loads((FIX / "sample_storyboard.json").read_text()) | {"video_id": vid}
    (tmp_path / "en.json").write_text(json.dumps(sb))
    r = run(rt, "texts", "--storyboard", str(tmp_path / "en.json"), "--out", str(tmp_path / "t.json"))
    items = json.loads((tmp_path / "t.json").read_text())
    assert r["items"] == len(items) > 5
    (tmp_path / "ta.json").write_text(json.dumps([dict(x, ta="த " + x["text"]) for x in items], ensure_ascii=False))
    r = run(rt, "localize", "--video-id", vid, "--storyboard", str(tmp_path / "en.json"), "--texts", str(tmp_path / "ta.json"))
    assert r["ok"] and r["video_id"].endswith("-051")


def test_backlog_guard_blocks_new_item_when_earlier_video_holds_a_future_slot():
    from ytauto.orchestration import cli
    from datetime import date
    ctx = SimpleNamespace(s=Settings.from_env({}))
    old = SimpleNamespace(video_id="VIDEO-20261126-101", state=State.AWAITING_APPROVAL,
                          proposed_publish_at="2026-11-27T12:00:00Z")
    done = SimpleNamespace(video_id="VIDEO-20261126-001", state=State.SCHEDULED,
                           proposed_publish_at="2026-11-26T14:00:00Z")
    orig = cli._today_local
    cli._today_local = lambda c: date(2026, 11, 27)
    try:
        assert cli._queued_future(ctx, [old, done]) == "VIDEO-20261126-101"
        assert cli._queued_future(ctx, [done]) is None
    finally:
        cli._today_local = orig
