"""Camera-man (scene-coder) code is checked before it is rendered."""
import json

import pytest

from studio.animation.custom_scene import SceneRejected, check, load, wrap_js

GOOD_HTML = '<div id="s2-root"><style>#s2-root .b{color:#fff}</style><div id="s2-box" class="b">API</div>' \
            '<img id="s2-img" src="assets/scene3.jpg"></div>'
GOOD_JS = 'tl.from("#"+P+"box",{opacity:0,y:40,duration:0.5},T0+0.2);'


def test_good_scene_passes():
    assert check(GOOD_HTML, GOOD_JS, "s2-") == []


@pytest.mark.parametrize("html,js,needle", [
    (GOOD_HTML.replace('id="s2-box"', 'id="box"'), GOOD_JS, "must start with"),
    (GOOD_HTML + "<script>alert(1)</script>", GOOD_JS, "script"),
    (GOOD_HTML.replace("assets/scene3.jpg", "https://evil.example/x.png"), GOOD_JS, "URL"),
    (GOOD_HTML.replace('class="b"', 'class="b" onclick="x()"'), GOOD_JS, "event handlers"),
    (GOOD_HTML, GOOD_JS + "fetch('/x');", "forbidden API"),
    (GOOD_HTML, "var tl = gsap.timeline();", "timeline"),
    (GOOD_HTML, "document.title='x'", "shared timeline"),
])
def test_bad_scenes_rejected(html, js, needle):
    assert any(needle in e for e in check(html, js, "s2-"))


def test_load_and_fallback_reason(tmp_path):
    (tmp_path / "scenes").mkdir()
    (tmp_path / "scenes" / "scene3.html").write_text(GOOD_HTML)
    (tmp_path / "scenes" / "scene3.js").write_text(GOOD_JS)
    html, js = load(tmp_path, "scene3", "s2-")
    assert "s2-box" in html and "(function(T0,DUR,P)" in wrap_js(js, 5.0, 4.0, "s2-")
    with pytest.raises(SceneRejected):
        load(tmp_path, "../etc", "s2-")
    with pytest.raises(SceneRejected, match="missing"):
        load(tmp_path, "scene9", "s2-")


def test_builder_uses_custom_scene_and_falls_back_safely(tmp_path):
    from studio.video import hyperframes as hfr
    if not (hfr.RENDERER_DIR / "node_modules" / "gsap").exists():
        pytest.skip("renderer deps not installed")
    sb = {"video_id": "VIDEO-20261125-001", "scenes": [
        {"narration": "a", "on_screen_text": "One", "visual": {"type": "custom", "code": "scene1"}},
        {"narration": "b", "on_screen_text": "Two", "visual": {"type": "custom", "code": "scene2"}}]}
    (tmp_path / "scenes").mkdir()
    (tmp_path / "scenes" / "scene1.html").write_text(GOOD_HTML.replace("s2-", "s0-"))
    (tmp_path / "scenes" / "scene1.js").write_text(GOOD_JS)
    (tmp_path / "scenes" / "scene2.html").write_text("<div id='bad'></div>")
    (tmp_path / "scenes" / "scene2.js").write_text(GOOD_JS)
    timings = [{"start": 0, "end": 3}, {"start": 3, "end": 6}]
    proj = hfr.build(sb, timings, [], tmp_path)
    doc = (proj / "index.html").read_text()
    rep = json.loads((tmp_path / "custom_scenes_report.json").read_text())
    assert "s0-box" in doc and rep[0]["status"] == "used"
    assert rep[1]["status"] == "fallback" and "must start with" in rep[1]["reason"]
