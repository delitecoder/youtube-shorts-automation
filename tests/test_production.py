"""Storyboard/metadata validation, captions, scheduling, research checks and a full render."""
import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from studio.analytics.snapshots import recommend_publish_time, summary
from studio.subtitles import captions
from studio.editorial.storyboard import validate_metadata, validate_storyboard
from studio.youtube.promotion.bluesky import build_post
from studio.youtube.schedule import next_slot, rfc3339
from studio.research.validate import validate_research

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
    ok = {"title": "How Hash Tables Find Data in One Step", "description": "Hash tables explained.\n#Shorts #Python #Coding",
          "tags": ["hash table", "python", "data structures", "programming", "coding", "algorithms", "dictionary",
                   "computer science"], "pinned_comment": "Where else would hashing help?", "category_id": "28"}
    assert validate_metadata(ok) == []
    assert validate_metadata(ok | {"title": "x" * 101})
    assert validate_metadata(ok | {"tags": ["t" * 60] * 10})
    assert validate_metadata(ok | {"description": "<b>"})
    assert validate_metadata(ok | {"title": "Guaranteed results"})
    assert validate_metadata(ok | {"description": "No hashtags here"})
    assert validate_metadata(ok | {"tags": ["one", "two"]})
    assert validate_metadata(ok | {"category_id": "10"})


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
def test_full_render_meets_output_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ASR", "0")   # a test tone is not speech; real-voice ASR: test_speech_captions
    from studio.audio.tts import ToneTTS
    from studio.video.render import render
    sb = copy.deepcopy(SB)
    sb["scenes"] = sb["scenes"][:5]
    for s in sb["scenes"]:
        s["narration"] = " ".join(s["narration"].split()[:4])   # keep the test fast
    tts = ToneTTS(words_per_second=4)
    clips = [tts.synthesize(s["narration"], tmp_path / f"s{i}.wav") for i, s in enumerate(sb["scenes"])]
    rep = render(sb, clips, tmp_path, tmp_path / "out.mp4", preset="ultrafast")
    assert rep["ok"], {k: v for k, v in rep.items() if k not in ("timings",)}
    assert (tmp_path / "captions.srt").read_text().strip()


def test_simple_string_visuals_are_normalised():
    from studio.editorial.storyboard import normalize_visual
    c = normalize_visual({"type": "comparison", "left": "Before: stateful, two-way", "right": "Now stateless"})
    assert c["left"] == {"title": "Before", "items": ["stateful", "two-way"]}
    assert c["right"] == {"title": "After", "items": ["Now stateless"]}
    t = normalize_visual({"type": "timeline", "events": ["2026-07-28: spec live", "no colon here"]})
    assert t["events"][0] == {"label": "2026-07-28", "text": "spec live"}
    assert t["events"][1]["label"] and isinstance(t["events"][1]["text"], str)


def test_hyperframes_composition_is_complete_and_captions_do_not_overlap(tmp_path):
    import re as _re
    from studio.video import hyperframes as hfr
    from studio.subtitles import captions as cap
    if not (hfr.RENDERER_DIR / "node_modules" / "gsap").exists():
        pytest.skip("npm ci --prefix renderer/hyperframes not run")
    sb = copy.deepcopy(SB)
    t, timings = 0.0, []
    for s in sb["scenes"]:
        timings.append({"start": t, "speech_start": t + 0.1, "speech_end": t + 4.0, "end": t + 4.5})
        t += 4.5
    caps = cap.build(sb["scenes"], timings)
    proj = hfr.build(sb, timings, caps, tmp_path)
    doc = (proj / "index.html").read_text()
    assert all(f'id="s{i}"' in doc for i in range(len(sb["scenes"])))
    assert "&nbsp;</span>" not in doc and "window.__timelines.main=tl" in doc
    spans = [(float(a), float(b)) for a, b in _re.findall(r'class="cap clip" data-start="([\d.]+)" data-duration="([\d.]+)"', doc)]
    for (s1, d1), (s2, _) in zip(spans, spans[1:]):
        assert s1 + d1 <= s2 + 1e-6


def test_free_slot_skips_taken_evenings():
    from studio.youtube.schedule import free_slot
    now = datetime(2026, 11, 24, 4, 30, tzinfo=timezone.utc)           # 10:00 IST
    assert rfc3339(free_slot(now, set())) == "2026-11-24T14:00:00Z"
    assert rfc3339(free_slot(now, {"2026-11-24T14:00:00Z"})) == "2026-11-25T14:00:00Z"
    late = datetime(2026, 11, 24, 13, 0, tzinfo=timezone.utc)          # 18:30 IST: too late for today
    assert rfc3339(free_slot(late, {"2026-11-25T14:00:00Z"})) == "2026-11-26T14:00:00Z"


def test_peak_slots_need_real_evidence():
    from studio.analytics.peak import choose_slots
    mon = datetime(2026, 11, 2, tzinfo=timezone.utc)          # a Monday
    def at(day, hour_ist, views):
        return (mon + timedelta(days=day, hours=hour_ist - 5.5), views)
    noise = [at(0, 8, 2)] * 3 + [at(1, 19.5, 1)] * 10           # tiny numbers -> keep default
    assert choose_slots(noise, "Asia/Kolkata", "Asia/Kolkata")["slots"]["mon"] == "19:30"
    strong = [at(0, 12, 200)] * 4 + [at(0, 19.5, 60)] * 4 + [at(2, 19.5, 50)] * 6
    assert choose_slots(strong, "Asia/Kolkata", "Asia/Kolkata")["slots"]["mon"] == "12:00"
