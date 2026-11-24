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
