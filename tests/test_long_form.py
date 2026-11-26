"""Long-form (16:9 system-design) videos: ids, discovery, storyboard, metadata, renderer layout."""
import json
from datetime import datetime, timezone

from ytauto.config import SYSTEM_DESIGN_SYLLABUS
from ytauto.production import hyperframes as hf
from ytauto.production import storyboard as sbm
from ytauto.store import is_long, next_video_id

from test_cli_and_service import run

NOW = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)


def test_long_ids_are_separate_and_match_the_gate_pattern():
    ids = ["VIDEO-20261125-001", "VIDEO-20261125-002"]
    assert next_video_id(ids, NOW) == "VIDEO-20261125-003"
    assert next_video_id(ids, NOW, long=True) == "VIDEO-20261125-101"
    assert next_video_id(ids + ["VIDEO-20261125-101"], NOW, long=True) == "VIDEO-20261125-102"
    assert next_video_id(ids + ["VIDEO-20261125-101"], NOW) == "VIDEO-20261125-003"
    assert is_long("VIDEO-20261125-101") and not is_long("VIDEO-20261125-001")


def _long_sb(n=36, words_each=30):
    scenes = []
    kinds = ["title", "bullets", "diagram", "stat", "custom", "comparison"]
    for i in range(n):
        k = kinds[i % len(kinds)]
        v = {"title": {"headline": "x"}, "bullets": {"items": ["a"]}, "diagram": {"nodes": ["a", "b"]},
             "stat": {"value": "1", "label": "x"}, "custom": {"code": f"scene{i + 1}"},
             "comparison": {"left": "A: x", "right": "B: y"}}[k]
        s = {"narration": ("Millions of requests hit one server, what breaks first? " if i == 0 else "") + "word " * words_each,
             "on_screen_text": "Load balancers", "transition": "fade", "visual": {"type": k, **v}}
        if i % 8 == 0:
            s["chapter"] = "Intro" if i == 0 else f"Part {i // 8}"
        scenes.append(s)
    return {"format": "long", "video_id": "VIDEO-20261125-101", "topic": "Load balancers",
            "hooks": ["Millions of requests hit one server", "b", "c"], "chosen_hook": 0,
            "titles": ["a", "b", "c"], "chosen_title": 0, "scenes": scenes, "closing_question": "Which?",
            "references": [{"url": "https://a.example/1"}, {"url": "https://a.example/2"},
                           {"url": "https://a.example/3"}]}


def test_long_storyboard_validates_and_chapters():
    sb = _long_sb()
    errs, warns = sbm.validate_storyboard(sb)
    assert errs == [], errs
    timings = [{"start": i * 12.0, "end": (i + 1) * 12.0} for i in range(len(sb["scenes"]))]
    chs = sbm.chapters(sb, timings)
    assert chs[0] == (0.0, "Intro") and len(chs) == 5
    block = sbm.chapter_block(chs)
    assert block.splitlines()[0] == "0:00 Intro" and "1:36 Part 1" in block


def test_long_storyboard_errors():
    sb = _long_sb(n=10)
    del sb["scenes"][0]["chapter"]
    errs, _ = sbm.validate_storyboard(sb)
    assert any("18–90" in e for e in errs) and any("scene 1 must start a chapter" in e for e in errs)


def test_long_metadata_rules():
    base = {"title": "How load balancers work", "tags": [f"t{i}" for i in range(10)],
            "category_id": "27", "pinned_comment": "Which one?",
            "description": "Text\n\n0:00 Intro\n1:00 Why\n2:00 How\n\n#systemdesign #backend #coding"}
    assert sbm.validate_metadata(base, long=True) == []
    bad = dict(base, description=base["description"] + " #Shorts")
    assert any("must not include #Shorts" in e for e in sbm.validate_metadata(bad, long=True))
    nochap = dict(base, description="Text #systemdesign #backend #coding")
    assert any("chapters" in e for e in sbm.validate_metadata(nochap, long=True))


def test_long_layout_and_palette(tmp_path, monkeypatch):
    nm = tmp_path / "nm" / "gsap" / "dist"
    nm.mkdir(parents=True)
    (nm / "gsap.min.js").write_text("")
    monkeypatch.setattr(hf, "RENDERER_DIR", tmp_path)
    (tmp_path / "node_modules").symlink_to(tmp_path / "nm")
    sb = _long_sb()
    for s in sb["scenes"]:
        if s["visual"]["type"] == "custom":
            s["visual"] = {"type": "title", "headline": "x"}
    timings = [{"start": i * 12.0, "end": (i + 1) * 12.0} for i in range(len(sb["scenes"]))]
    proj = hf.build(sbm.normalize(sb), timings, [], tmp_path / "w")
    doc = (proj / "index.html").read_text()
    assert 'data-width="1920" data-height="1080"' in doc
    assert "CHAPTER 2" in doc and 'id="chp0"' in doc
    pal = hf.palette_for(sb)
    assert pal["bg"] in doc
    # consecutive days get different styles
    a = hf.palette_for({"video_id": "VIDEO-20261125-001"})["name"]
    b = hf.palette_for({"video_id": "VIDEO-20261126-001"})["name"]
    assert a != b


def test_discover_long_returns_next_syllabus_topic(tmp_path):
    d = run(tmp_path / "rt", "discover", "--format", "long")
    assert d["action"] == "research" and d["format"] == "long"
    assert d["theme"]["topic"] == SYSTEM_DESIGN_SYLLABUS[0] and d["theme"]["key"] == "system_design"
    item = run(tmp_path / "rt", "new-item", "--format", "long")
    assert item["video_id"].endswith("-101")
    d2 = run(tmp_path / "rt", "discover", "--format", "long")
    assert d2["action"] == "resume" and d2["video_id"] == item["video_id"]
    # the Short pipeline is not blocked by the long item
    assert run(tmp_path / "rt", "discover")["action"] == "research"


def test_stickman_scene_builds_rig_and_alternates_looks(tmp_path, monkeypatch):
    nm = tmp_path / "nm" / "gsap" / "dist"
    nm.mkdir(parents=True)
    (nm / "gsap.min.js").write_text("")
    monkeypatch.setattr(hf, "RENDERER_DIR", tmp_path)
    (tmp_path / "node_modules").symlink_to(tmp_path / "nm")
    sb = {"video_id": "VIDEO-20261126-001", "scenes": [
        {"narration": "x", "on_screen_text": "Hi", "visual": {"type": "stickman", "text": "200M payments", "prop": "server",
         "actions": [{"at": 0, "pose": "stand", "face": "happy"}, {"at": 0.5, "pose": "shocked", "face": "surprised"}]}}]}
    doc = (hf.build(sb, [{"start": 0, "end": 4}], [], tmp_path / "w") / "index.html").read_text()
    assert 's0st-torso' in doc and "feTurbulence" in doc and "200M payments" in doc and "#FDFDFC" in doc
    assert hf.palette_for(sb)["name"] == "paper"
    assert hf.palette_for(dict(sb, video_id="VIDEO-20261127-001"))["name"] != "paper"   # chalkboard day
    errs, _ = sbm.validate_storyboard(dict(sb, scenes=[sb["scenes"][0]] * 5, hooks=["a"] * 3, titles=["a"] * 3,
                                            chosen_hook=0, chosen_title=0, topic="t", closing_question="Why?",
                                            references=[{"url": "https://a.b/1"}] * 3))
    assert not any("visual" in e for e in errs)


def test_three_scene_builds_and_copies_runtime(tmp_path, monkeypatch):
    nm = tmp_path / "node_modules"
    (nm / "gsap" / "dist").mkdir(parents=True)
    (nm / "gsap" / "dist" / "gsap.min.js").write_text("")
    (nm / "three" / "build").mkdir(parents=True)
    (nm / "three" / "build" / "three.module.min.js").write_text("")
    (tmp_path / "three_scenes.js").write_text("export function mountAll(){}")
    monkeypatch.setattr(hf, "RENDERER_DIR", tmp_path)
    sb = {"video_id": "VIDEO-20261126-001", "scenes": [{"narration": "x", "on_screen_text": "Load", "visual":
          {"type": "three", "template": "servers", "nodes": ["Users", "LB", "A", "B"]}}]}
    proj = hf.build(sb, [{"start": 0, "end": 3}], [], tmp_path / "w")
    doc = (proj / "index.html").read_text()
    assert "data-three=" in doc and "buildReady.three" in doc and "mountAll(THREE)" in doc
    assert (proj / "three.module.min.js").exists() and (proj / "three_scenes.js").exists()
    assert sbm.slop_check({"scenes": [{"visual": {"type": "three", "template": "spaceship"}}]})
