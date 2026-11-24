"""Claude usage planner: full / lean / wait / weekly_stop and cost learning."""
from datetime import datetime, timedelta, timezone

from ytauto.orchestration import budget as b

NOW = datetime(2026, 11, 24, 4, 37, tzinfo=timezone.utc)          # 10:07 IST


def ts(hours):
    return int((NOW + timedelta(hours=hours)).timestamp())


def test_plenty_of_budget_goes_full():
    p = b.plan(0.05, ts(4), 0.15, ts(24 * 4.5), now=NOW)
    assert p["decision"] == "go_full"


def test_weekly_pacing_switches_to_lean():
    # 8 % of the week left with 4 days to go: a full video (~2.9 %) would starve later days
    p = b.plan(0.05, ts(4), 0.89, ts(24 * 4), now=NOW)
    assert p["decision"] == "go_lean" and "weekly pacing" in p["reason"]


def test_weekly_limit_over_stops():
    assert b.plan(0.05, ts(4), 0.96, ts(30), now=NOW)["decision"] == "weekly_stop"
    assert b.plan(0.05, ts(4), 0.5, ts(30), now=NOW, seven_status="rejected")["decision"] == "weekly_stop"


def test_five_hour_tight_but_lean_fits():
    p = b.plan(0.85, ts(3), 0.2, ts(100), now=NOW)
    assert p["decision"] == "go_lean"


def test_five_hour_exhausted_and_reset_soon_waits():
    p = b.plan(0.95, ts(1), 0.2, ts(100), now=NOW)
    assert p["decision"] == "wait" and 55 <= p["resume_after_minutes"] <= 70


def test_cost_is_learned_and_mid_run_resets_ignored():
    s = b.record_sample([], {"five_hour": 0.2, "seven_day": 0.1}, {"five_hour": 0.28, "seven_day": 0.115})
    s = b.record_sample(s, {"five_hour": 0.5, "seven_day": 0.2}, {"five_hour": 0.56, "seven_day": 0.21})
    s = b.record_sample(s, {"five_hour": 0.9, "seven_day": 0.3}, {"five_hour": 0.1, "seven_day": 0.31})  # 5h reset mid-run
    cost = b.learned_cost(s)
    assert abs(cost["five_hour"] - 0.07) < 0.011 and abs(cost["seven_day"] - 0.011) < 0.005



def test_audit_rules_turn_numbers_into_guidance():
    from ytauto.analytics.audit import analyse, email_text
    rep = {"date": "2026-11-24", "last7": {"views": 36, "subscribersGained": 0}, "prev7": {"views": 900},
           "last28": {"views": 1300}, "prev28": {"views": 30},
           "videos": [{"video": f"v{i}", "views": v, "averageViewPercentage": p, "averageViewDuration": 8,
                       "likes": 0, "shares": 0, "subscribersGained": 0, "ours": False, "title": f"T{i}"}
                      for i, (v, p) in enumerate([(1300, 52), (5, 4), (3, 3), (2, 5)])],
           "traffic": {"SHORTS": 30, "YT_SEARCH": 40, "EXT_URL": 30}}
    a = analyse(rep)
    g = a["guidance"]
    assert g["target_seconds"] == 45 and "first_frame" in g and "seo" in g and "series" in g and "topic" in g
    assert a["top"][0]["title"] == "T0" and a["trend"]["views_7d_change_pct"] == -96.0
    txt = email_text(a)
    assert "Daily views audit" in txt and "What the next videos will change" in txt


def test_audit_with_no_data_does_not_invent():
    from ytauto.analytics.audit import analyse
    a = analyse({"date": "2026-11-24", "videos": []})
    assert a["top"] == [] and "No video data" in a["suggestions"][0]
