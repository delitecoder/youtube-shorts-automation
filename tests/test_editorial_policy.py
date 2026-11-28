"""Editorial policy gates (rules.md §1-§3) enforced in code."""
from datetime import date

from studio.editorial.policy import research_contract, scene_contract, topic_gate
from studio.research.validate import validate_research

SRC = [{"title": f"s{i}", "url": f"https://example.com/{i}", "publisher": "p", "published": "2026-01-01",
        "accessed": "2026-11-28"} for i in range(3)]


def research(**kw):
    base = {"schema": 2, "topic": "How hash tables work", "topic_key": "hash-tables", "category": "programming",
            "angle": "one-step lookups", "sources": SRC,
            "facts": [{"id": "f1", "claim": "dicts are hash tables", "source_urls": ["https://example.com/0"],
                       "confidence": "high", "status": "verified"}]}
    return base | kw


def test_movie_trailer_topic_is_rejected_unless_it_teaches_technology():
    r = research(topic="New superhero movie trailer breakdown", angle="best scenes")
    assert any("off-topic" in e for e in topic_gate(r))
    ok = r | {"tech_lesson": "how the studio rendered the de-aging shots with neural face replacement and "
                             "what that pipeline costs in GPU hours"}
    assert topic_gate(ok) == []


def test_research_contract_requires_status_access_date_and_verification():
    errs, _ = research_contract(research())
    assert errs == []
    bad = research(facts=[{"id": "f1", "claim": "X is 10x faster", "source_urls": ["https://example.com/0"],
                           "high_impact": True}])
    errs, _ = research_contract(bad)
    assert any("status" in e for e in errs) and any("verified_by" in e for e in errs)
    errs, _ = research_contract(research(sources=[{"url": "https://e.com", "published": "2026-01-01"}]))
    assert any("accessed" in e for e in errs)


def test_validate_research_applies_topic_gate():
    e, _ = validate_research(research(topic="Celebrity gossip of the week"), date(2026, 11, 28), [])
    assert any("off-topic" in x for x in e)


def _sb(**kw):
    scenes = [{"narration": "Why do lookups feel instant?", "purpose": "hook with the core question",
               "visual_purpose": "show a search box returning instantly", "visual": {"type": "title"}},
              {"narration": "A billion names would take ages to scan.", "purpose": "show the slow way",
               "visual_purpose": "counter racing upward", "claims": ["f1"], "visual": {"type": "stat"}}]
    return {"schema": 2, "story_structure": "problem_mechanism_solution", "claims": [{"id": "f1"}],
            "scenes": scenes} | kw


def test_schema2_scene_contract():
    assert scene_contract(_sb()) == ([], [])
    sb = _sb()
    sb["scenes"][0].pop("purpose")
    sb["scenes"][1]["claims"] = []
    errs, _ = scene_contract(sb)
    assert any("'purpose' missing" in e for e in errs) and any("cites no researched claim" in e for e in errs)
    assert any("story_structure" in e for e in scene_contract(_sb(story_structure="whatever"))[0])


def test_legacy_storyboards_only_warn():
    legacy = {"scenes": [{"narration": "It takes 5 steps.", "visual": {"type": "title"}}]}
    errs, warns = scene_contract(legacy)
    assert errs == [] and warns and all(w.startswith("legacy storyboard") for w in warns)


def test_auto_scenes_are_exempt():
    sb = _sb()
    sb["scenes"].append({"narration": "Subscribe for more", "auto": True, "visual": {"type": "subscribe"}})
    assert scene_contract(sb) == ([], [])
