"""Daily content theme rotation."""
from datetime import date, timedelta

from ytauto.config import ALLOWED_CATEGORIES, CONTENT_ROTATION, CONTENT_THEMES, theme_for

from test_cli_and_service import run


def test_rotation_covers_every_weekday():
    assert len(CONTENT_ROTATION) == 7
    assert set(CONTENT_ROTATION) <= set(CONTENT_THEMES)


def test_shorts_are_daily_tech_news():
    monday = date(2026, 11, 23)
    keys = [theme_for(monday + timedelta(days=i))["key"] for i in range(7)]
    assert keys == ["trending_news"] * 7
    t = theme_for(date(2026, 11, 26))
    assert t["label"] == "Tech News" and t["brief"]
    # a ThemePlan row still overrides one day
    t = theme_for(date(2026, 11, 26), [{"date": "2026-11-26", "theme": "motivation"}])
    assert t["key"] == "motivation"


def test_new_categories_allowed():
    assert {"tech stories", "motivation"} <= set(ALLOWED_CATEGORIES)


def test_discover_reports_theme(tmp_path):
    d = run(tmp_path / "rt", "discover")
    assert d["action"] == "research"
    assert d["theme"]["key"] in CONTENT_THEMES and d["theme"]["label"]


def test_theme_plan_overrides_rotation():
    day = date(2026, 11, 25)                                      # a Wednesday (tech news)
    assert theme_for(day, [])["key"] == "trending_news"
    plan = [{"date": "25/11/2026", "theme": "Tech Story", "details": "about the first computer bug"}]
    t = theme_for(day, plan)
    assert t["key"] == "tech_story" and "first computer bug" in t["brief"] and t["source"] == "ThemePlan"
    t = theme_for(day, [{"date": "2026-11-25", "theme": "AI in healthcare", "details": ""}])
    assert t["key"] == "custom" and "AI in healthcare" in t["brief"]
    assert theme_for(day, [{"date": "2026-11-26", "theme": "motivation", "details": ""}])["key"] == "trending_news"
    assert theme_for(day, [{"date": "bad", "theme": "motivation", "details": ""}])["key"] == "trending_news"


def test_music_tempo_analysis_and_theme_match(tmp_path):
    import numpy as np
    from ytauto.production.sound import SR, analyze_music, mood_distance, synth_music, write_wav
    n = int(20 * SR)
    for bpm in (90, 128):
        x = np.zeros(n, np.float32)
        for i in range(0, n, int(SR * 60 / bpm)):
            x[i:i + 400] += np.sin(np.arange(400) * 2 * np.pi * 1000 / SR) * np.linspace(1, 0, 400)
        write_wav(tmp_path / f"t{bpm}.wav", x * 0.8)
        f = analyze_music(tmp_path / f"t{bpm}.wav")
        assert abs(f["bpm"] - bpm) < 4, f
    slow = analyze_music(tmp_path / "t90.wav")
    assert mood_distance(slow, "tech_story") < mood_distance(slow, "trending_search")
    write_wav(tmp_path / "s.wav", synth_music(20, 1, "trending_search"))
    assert analyze_music(tmp_path / "s.wav")["bpm"] > 100
