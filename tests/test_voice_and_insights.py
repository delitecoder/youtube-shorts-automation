"""Free-tier voice (cache, clean text, caps) and audience insights driving the next topic."""
from types import SimpleNamespace

from ytauto.analytics import audit as au
from ytauto.config import TTS_FREE_TIER_CAP, Settings
from ytauto.integrations import tts
from ytauto.orchestration.cli import rank_topics
from ytauto.production import storyboard as sbm


class MemStore:
    def __init__(self):
        self.c = {}

    def get_config(self, k, d=""):
        return self.c.get(k, d)

    def set_config(self, k, v):
        self.c[k] = v


def test_budget_never_exceeds_free_tier():
    assert Settings.from_env({"TTS_MONTHLY_CHAR_BUDGET": "5000000"}).tts_monthly_char_budget == TTS_FREE_TIER_CAP
    assert Settings.from_env({}).tts_monthly_char_budget == 850_000
    assert TTS_FREE_TIER_CAP < 1_000_000


def test_voice_cache_costs_nothing_on_rerender(tmp_path, monkeypatch):
    calls = []

    class Fake:
        name, voice = "google", "v1"

        def preflight(self, texts):
            calls.append(("pre", sum(len(t) for t in texts)))

        def synthesize(self, text, out):
            calls.append(("syn", text))
            out.write_bytes(b"RIFF")
            return out
    monkeypatch.setattr(tts, "build_provider", lambda name, s, st: Fake())
    s = SimpleNamespace(tts_provider="google", tts_fallbacks=())
    clips, prov, _ = tts.synthesize_all(["Hello   world.\n", "Second scene."], tmp_path, s, None, log=lambda m: None)
    assert prov == "google" and [c for c in calls if c[0] == "syn"] == [("syn", "Hello world."), ("syn", "Second scene.")]
    calls.clear()
    tts.synthesize_all(["Hello world.", "Second scene changed."], tmp_path, s, None, log=lambda m: None)
    assert calls == [("pre", len("Second scene changed.")), ("syn", "Second scene changed.")]
    calls.clear()
    tts.synthesize_all(["Hello world.", "Second scene changed."], tmp_path, s, None, log=lambda m: None)
    assert calls == []                                  # fully cached: no API call, no characters


def test_narration_char_limit():
    sb = {"video_id": "VIDEO-20261125-001", "topic": "t", "hooks": ["a", "b", "c"], "chosen_hook": 0,
          "titles": ["a", "b", "c"], "chosen_title": 0, "closing_question": "Why?",
          "scenes": [{"narration": "word " * 60, "on_screen_text": "x", "visual": {"type": "title", "headline": "x"}}] * 5,
          "references": [{"url": "https://a.b/1"}] * 3}
    errs, _ = sbm.validate_storyboard(sb)
    assert any("free tier" in e for e in errs)


def test_audit_topic_signals_and_guidance():
    rep = {"date": "2026-11-25", "traffic": {"SHORTS": 50, "YT_SEARCH": 50},
           "search_terms": [{"term": "load balancer explained", "views": 40}, {"term": "anthropic ipo", "views": 9}],
           "suggested_next_to": [{"title": "Consistent Hashing in 5 minutes", "views": 30}],
           "videos": [{"video": "a", "title": "Tech News: X", "views": 100, "averageViewPercentage": 70,
                       "averageViewDuration": 30, "likes": 5, "comments": 1, "shares": 1, "subscribersGained": 1,
                       "ours": True, "format": "short"}]}
    res = au.analyse(rep)
    sig = res["topic_signals"]
    assert sig["search_terms"][0] == "load balancer explained"
    assert sig["suggested_next_to"] == ["Consistent Hashing in 5 minutes"]
    assert res["guidance"]["audience_terms"][0] == "load balancer explained"
    assert any("searching" in x for x in res["suggestions"])


def test_next_long_topic_follows_the_audience():
    topics = ["What happens when you type a URL and press Enter", "Load balancers", "Consistent hashing"]
    assert rank_topics(topics, {}) == (topics[0], 0)
    t, score = rank_topics(topics, {"search_terms": ["how load balancer works"]})
    assert t == "Load balancers" and score > 0
    t, _ = rank_topics(topics, {"suggested_next_to": ["Consistent Hashing in 5 minutes"]})
    assert t == "Consistent hashing"


def test_off_topic_searches_are_not_used_for_planning():
    rep = {"date": "2026-11-25", "videos": [],
           "search_terms": [{"term": "bomma padam", "views": 50}, {"term": "birthday app", "views": 5},
                            {"term": "sql drop vs truncate", "views": 3}]}
    sig = au.topic_signals(rep)
    assert sig["search_terms"] == ["birthday app", "sql drop vs truncate"]
    assert sig["other_searches"] == ["bomma padam"]
