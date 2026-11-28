"""Free-tier voice (cache, clean text, caps) and audience insights driving the next topic."""
from types import SimpleNamespace

from studio.analytics import audit as au
from studio.core.config import TTS_FREE_TIER_CAP, Settings
from studio.audio import tts
from studio.cli.common import rank_topics
from studio.editorial import storyboard as sbm


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


def test_slop_phrases_and_spam_hashtags_rejected():
    sb = {"scenes": [{"narration": "Let's dive in to the game-changer AI."}]}
    assert sbm.slop_check(sb)
    m = {"title": "Anthropic IPO opens 34% up", "tags": [f"t{i}" for i in range(9)], "category_id": "28",
         "pinned_comment": "Buy?", "description": "Line\n#Shorts #AI #TikTok #viral"}
    errs = sbm.validate_metadata(m)
    assert any("#tiktok" in e for e in errs) and not any("#viral" in e for e in errs)


def test_reach_hashtags_added_once():
    m = {"title": "t", "tags": ["ai"], "description": "Hook line\n#Shorts #AI"}
    out = sbm.add_reach_tags(m, long=False)
    tags_line = out["description"].splitlines()[-1]
    assert "#viral" in tags_line and "#trending" in tags_line and tags_line.lower().split().count("#shorts") == 1
    assert {"shorts", "viral", "trending"} <= set(out["tags"])
    assert sbm.add_reach_tags(out, long=False)["description"] == out["description"]
    lng = sbm.add_reach_tags({"description": "x\n#SystemDesign", "tags": []}, long=True, lang="ta")
    assert "#shorts" not in lng["description"].lower() and "#tamil" in lng["description"]


def test_reach_uses_channel_history_but_never_brand_or_wrong_language_tags():
    ch = {"hashtags": ["#shorts", "#delitecart", "#viral", "#trending", "#ai", "#tamil", "#diwali", "#trendingshorts"],
          "tags": ["shorts", "sql delete vs drop", "viral", "trending", "tamil"]}
    en = sbm.add_reach_tags({"description": "Hook\n#CDN #Netflix", "tags": ["cdn"]}, long=False, channel=ch)
    line = en["description"].splitlines()[-1].lower()
    assert "#viral" in line and "#trendingshorts" in line and "#ai" in line
    assert "#delitecart" not in line and "#diwali" not in line and "#tamil" not in line
    assert "sql delete vs drop" not in en["tags"]
    ta = sbm.add_reach_tags({"description": "x\n#CDN", "tags": []}, long=True, lang="ta", channel=ch)
    assert "#tamil" in ta["description"] and "short" not in ta["description"].lower()
