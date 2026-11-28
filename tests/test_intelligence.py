"""Style registry, content memory, VideoFacts (unknown != 0) and the controlled experiment engine."""
import json

import pytest

from studio.analytics import facts
from studio.animation import styles
from studio.experimentation import engine
from studio.memory import content
from studio.storage.store import JsonFileStore


@pytest.fixture
def store(tmp_path):
    return JsonFileStore(tmp_path / "s.json")


# ---------------- styles
def test_style_choice_suits_topic_and_avoids_recent():
    st, why = styles.choose("system design", [])
    assert "system design" in [c.lower() for c in st["categories"]] and "suits" in why
    hist = [{"format": "short", "style_id": f"{st['id']}@v1"}]
    st2, _ = styles.choose("system design", hist)
    assert st2["id"] != st["id"]
    assert styles.choose("x", [], assigned="neon-lab")[0]["id"] == "neon-lab"


def test_apply_writes_explicit_style_used_by_renderer():
    from studio.video.hyperframes import palette_for, style_for
    sb = styles.apply({"video_id": "VIDEO-20261128-001", "scenes": []}, styles.by_id("ember-investigation"))
    assert sb["style_id"] == "ember-investigation@v1"
    assert palette_for(sb)["name"] == "ember" and style_for(sb)["title"] == "grey_to_ink"


# ---------------- content memory
def sb_of(text, hook="Why is your API slow?", layouts=("title", "stat", "diagram", "bullets")):
    scenes = [{"narration": f"{text} part {i}.", "visual": {"type": t}} for i, t in enumerate(layouts)]
    return {"hooks": [hook], "chosen_hook": 0, "titles": ["T"], "chosen_title": 0, "scenes": scenes}


SCRIPT = ("Every request to your API waits on the database because the query scans the whole table instead "
          "of using an index, and the fix is a composite index on the two columns you filter by")


def test_memory_blocks_near_verbatim_reuse_but_not_a_new_take(store):
    content.record(store, "VIDEO-1", sb_of(SCRIPT))
    same = content.check(store, sb_of(SCRIPT))
    assert any("identical hook" in b for b in same["block"]) and any("script" in b for b in same["block"])
    assert any("identical scene layouts" in b for b in same["block"])
    fresh = sb_of("Caches keep recent answers close to the CPU so the next lookup skips the slow trip to memory "
                  "entirely, which is why a warm cache feels instant", hook="What makes a cache warm?",
                  layouts=("question", "timeline", "stat", "comparison"))
    assert content.check(store, fresh)["block"] == []


def test_memory_detects_near_copied_animation_code(store, tmp_path):
    d1, d2 = tmp_path / "a", tmp_path / "b"
    d1.mkdir(), d2.mkdir()
    code = "\n".join(f'tl.from("#s0-box{i}",{{x:{i * 40},opacity:0,duration:0.4}},{i * 0.2});' for i in range(30))
    (d1 / "scene1.js").write_text(code)
    (d2 / "scene3.js").write_text(code.replace("0.4", "0.45").replace("#s0-", "#s2-"))
    content.record(store, "VIDEO-1", sb_of(SCRIPT), scenes_dir=d1)
    r = content.check(store, sb_of("totally different words about packets " * 5, hook="New?"), scenes_dir=d2)
    assert any("near-copy" in b for b in r["block"])


def test_tamil_localisation_is_exempt(store):
    content.record(store, "VIDEO-1", sb_of(SCRIPT))
    assert content.check(store, sb_of(SCRIPT) | {"language": "ta"})["block"] == []


# ---------------- facts
def test_missing_metric_is_unknown_not_zero():
    f = facts.from_snapshot({"video_id": "VIDEO-20261120-001", "views": "120", "averageViewPercentage": "",
                             "published_at": "2026-11-20T14:00:00Z"})
    assert f["views"] == "120" and f["avg_view_pct"] == "" and "avg_view_pct" in f["unknown_metrics"]
    assert "impressions" in f["unknown_metrics"] and facts.metric(f, "avg_view_pct") is None
    assert f["format"] == "short" and f["lang"] == "en"


# ---------------- experiments
def test_allocation_is_deterministic_and_close_to_policy(store):
    modes = [engine.plan(store, f"VIDEO-202611{d:02d}-{n:03d}")["mode"] for d in range(1, 29) for n in (1, 2, 3, 4)]
    share = {m: modes.count(m) / len(modes) for m in ("proven", "variation", "exploration")}
    assert 0.55 <= share["proven"] <= 0.85 and share["variation"] > 0.05 and share["exploration"] > 0.0
    again = engine.plan(store, "VIDEO-20261101-001")
    assert again["reused"] is True


def _variation_ids(store, k):
    ids, d = [], 1
    while len(ids) < k:
        vid = f"VIDEO-2026{(d // 28) + 1:02d}{d % 28 + 1:02d}-001"
        d += 1
        if engine._u(vid, "alloc") >= 0.7 and engine._u(vid, "alloc") < 0.9:
            ids.append(vid)
    return ids


def _facts(store, vid, pct, days=10):
    store.append("VideoFacts", {"snapshot_at": f"2026-12-{days:02d}T00:00:00Z", "video_id": vid, "format": "short",
                                "lang": "en", "days_live": str(days), "avg_view_pct": "" if pct is None else str(pct)})


def test_one_viral_video_never_wins_and_unknown_is_skipped(store):
    ids = _variation_ids(store, 2)
    plans = [engine.plan(store, v) for v in ids]
    assert plans[0]["exp_id"] and plans[0]["exp_id"] == plans[1]["exp_id"]
    assert {p["arm"] for p in plans} == {"control", "treatment"}             # arms balanced
    t = next(v for v, p in zip(ids, plans) if p["arm"] == "treatment")
    c = next(v for v, p in zip(ids, plans) if p["arm"] == "control")
    _facts(store, t, 95.0)
    _facts(store, c, None)                                                    # metric unavailable
    r = engine.evaluate(store)[0]
    assert r["status"] == "running" and "inconclusive" in r["conclusion"]
    assert c in json.loads(r["evidence"])["unknown_or_too_young"]


def test_clear_effect_with_enough_samples_concludes_and_recommends(store):
    ids = _variation_ids(store, 12)
    plans = {v: engine.plan(store, v) for v in ids}
    for v, p in plans.items():
        _facts(store, v, 80.0 + (v[-5] == "1") if p["arm"] == "treatment" else 60.0 + 0.5 * (int(v[-6]) % 2))
    r = engine.evaluate(store)[0]
    assert r["status"] == "concluded" and float(r["ci_low"]) > 0
    recs = engine.recommendations(store)
    rec = next(iter(recs.values()))
    assert rec["choice"] == r["treatment"] and rec["evidence"] == r["exp_id"]
    nxt = engine.plan(store, "VIDEO-20270101-001")
    assert nxt["choices"][r["dimension"]] == r["treatment"] or nxt["mode"] != "proven"
