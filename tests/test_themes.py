"""Daily content theme rotation."""
from datetime import date, timedelta

from ytauto.config import ALLOWED_CATEGORIES, CONTENT_ROTATION, CONTENT_THEMES, theme_for

from test_cli_and_service import run


def test_rotation_covers_every_weekday_and_theme():
    assert len(CONTENT_ROTATION) == 7
    assert set(CONTENT_ROTATION) == set(CONTENT_THEMES)


def test_theme_for_weekdays():
    monday = date(2026, 11, 23)
    keys = [theme_for(monday + timedelta(days=i))["key"] for i in range(7)]
    assert keys == ["trending_search", "tech_story", "trending_news", "motivation",
                    "trending_search", "tech_story", "trending_news"]
    t = theme_for(date(2026, 11, 26))
    assert t["label"] == "Tech Motivation" and t["brief"]


def test_new_categories_allowed():
    assert {"tech stories", "motivation"} <= set(ALLOWED_CATEGORIES)


def test_discover_reports_theme(tmp_path):
    d = run(tmp_path / "rt", "discover")
    assert d["action"] == "research"
    assert d["theme"]["key"] in CONTENT_THEMES and d["theme"]["label"]
