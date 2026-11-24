"""Storyboard/metadata validation, captions, scheduling, research checks and a full render."""
import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ytauto.analytics.youtube_analytics import recommend_publish_time, summary
from ytauto.production import captions
from ytauto.production.storyboard import validate_metadata, validate_storyboard
from ytauto.promotion.bluesky import build_post
from ytauto.publishing.schedule import next_slot, rfc3339
from ytauto.research.validate import validate_research

FIX = Path(__file__).parent / "fixtures"
SB = json.loads((FIX / "sample_storyboard.json").read_text())


def test_sample_storyboard_valid():
    errs, warns = validate_storyboard(SB)
    assert errs == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s.update(scenes=s["scenes"][:4]), "need 5–8"),
    (lambda s: s.update(hooks=s["hooks"][:2]), "3 hook"),
    (lambda s: s.update(references=[]), "references"),
    (lambda s: s["scenes"][0]["visual"].update(type="video"), "visual.type"),
    (lambda s: s.update(closing_question="Thanks for watching."), "question"),
    (lambda s: s["scenes"][1].update(narration="word " * 300), "too long"),
])
def test_storyboard_errors(mutate, needle):
    sb = copy.deepcopy(SB)
    mutate(sb)
    errs, _ = validate_storyboard(sb)
    assert any(needle in e for e in errs), errs


def test_metadata_validation():
    ok = {"title": "How Hash Tables Find Data in One Step", "description": "Hash tables explained. #Shorts",
          "tags": ["hash table", "python"], "pinned_comment": "Where else would hashing help?"}
    assert validate_metadata(ok) == []
    assert validate_metadata(ok | {"title": "x" * 101})
    assert validate_metadata(ok | {"tags": ["t" * 60] * 10})
    assert validate_metadata(ok | {"description": "<b>"})
    assert validate_metadata(ok | {"title": "Guaranteed results"})


def test_caption_timing_monotonic_and_bounded():
    scenes = SB["scenes"]
    timings = [{"speech_start": i * 5.0, "speech_end": i * 5.0 + 4.0} for i in range(len(scenes))]
    caps = captions.build(scenes, timings)
    starts = [c["start"] for c in caps]
    assert starts == sorted(starts)
    assert all(len(" ".join(w[0] for w in c["words"])) <= 30 for c in caps)
    assert caps[-1]["end"] <= timings[-1]["speech_end"] + 1e-6
    assert "-->" in captions.to_srt(caps)


def test_next_slot_ist():
    # 10:00 IST -> same day 19:30 IST (14:00 UTC)
    assert rfc3339(next_slot(datetime(2026, 11, 24, 4, 30, tzinfo=timezone.utc))) == "2026-11-24T14:00:00Z"
    # 18:00 IST is inside the 2 h lead -> next day
    assert rfc3339(next_slot(datetime(2026, 11, 24, 12, 30, tzinfo=timezone.utc))) == "2026-11-25T14:00:00Z"


def research(**kw):
    r = {"topic": "t", "topic_key": "hash-tables", "category": "programming", "angle": "a",
         "sources": [{"title": f"s{i}", "url": f"https://e.com/{i}", "publisher": "p", "published": "2026-01-01"}
                     for i in range(3)],
         "facts": [{"claim": "c", "source_urls": ["https://e.com/0"], "confidence": "high"}]}
    r.update(kw)
    return r


def test_research_validation_and_dedupe():
    today = date(2026, 11, 24)
    assert validate_research(research(), today, [])[0] == []
    assert validate_research(research(category="sports"), today, [])[0]
    assert validate_research(research(sources=[]), today, [])[0]
    hist = [{"topic_key": "hash-tables", "researched_at": "2026-10-01T00:00:00+00:00"}]
    assert any("already covered" in e for e in validate_research(research(), today, hist)[0])
    bad_fact = research(facts=[{"claim": "x", "source_urls": ["https://other"], "confidence": "high"}])
    assert any("not backed" in e for e in validate_research(bad_fact, today, [])[0])


def test_analytics_never_invents_and_needs_data_for_time_change():
    assert summary([], {})["videos_with_data"] == 0
    rows = [{"snapshot_at": "2026-11-20", "youtube_video_id": "a", "views": "100",
             "averageViewPercentage": "80", "likes": "5", "published_at": "2026-11-10T14:00:00+00:00"}]
    assert summary(rows, {"a": "A"})["best"][0]["title"] == "A"
    rec = recommend_publish_time(rows, "Asia/Kolkata")
    assert rec["changed"] is False and "insufficient" in rec["reason"]


def test_bluesky_post_link_facet():
    text, facets = build_post("Hash tables", "https://youtube.com/shorts/abc", ["python"])
    idx = facets[0]["index"]
    assert text.encode()[idx["byteStart"]:idx["byteEnd"]] == b"https://youtube.com/shorts/abc"


@pytest.mark.slow
def test_full_render_meets_output_spec(tmp_path):
    from ytauto.integrations.tts import ToneTTS
    from ytauto.production.render import render
    sb = copy.deepcopy(SB)
    sb["scenes"] = sb["scenes"][:5]
    for s in sb["scenes"]:
        s["narration"] = " ".join(s["narration"].split()[:4])   # keep the test fast
    tts = ToneTTS(words_per_second=4)
    clips = [tts.synthesize(s["narration"], tmp_path / f"s{i}.wav") for i, s in enumerate(sb["scenes"])]
    rep = render(sb, clips, tmp_path, tmp_path / "out.mp4", preset="ultrafast")
    assert rep["ok"], rep["checks"]
    assert (tmp_path / "captions.srt").read_text().strip()


def test_simple_string_visuals_are_normalised():
    from ytauto.production.storyboard import normalize_visual
    c = normalize_visual({"type": "comparison", "left": "Before: stateful, two-way", "right": "Now stateless"})
    assert c["left"] == {"title": "Before", "items": ["stateful", "two-way"]}
    assert c["right"] == {"title": "After", "items": ["Now stateless"]}
    t = normalize_visual({"type": "timeline", "events": ["2026-07-28: spec live", "no colon here"]})
    assert t["events"][0] == {"label": "2026-07-28", "text": "spec live"}
    assert t["events"][1]["label"] and isinstance(t["events"][1]["text"], str)
