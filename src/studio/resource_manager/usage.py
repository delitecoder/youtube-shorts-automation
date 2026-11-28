"""Claude usage budget planner (deterministic; no model calls).

The orchestrating session reads its real limits from the session API (`get_session` →
rate_limit_info, `list_events` kinds=["rate_limit_event"] → unifiedWindows.five_hour / seven_day
utilization 0–1 and resetsAt epoch seconds) and passes them here. The planner decides:

  go_full   – enough room in the 5-hour window and the weekly pacing allows a normal video
  go_lean   – fits only with the lean plan (no revision loop, metadata-only image checks, no
              Reddit drafts, one research pass)
  wait      – 5-hour window nearly empty but it resets soon: start right after the reset
              (send_later) instead of stopping half-way
  weekly_stop – the weekly allowance cannot cover even a lean video: send the owner an alert
Per-video cost is learned from previous runs (utilization before/after), stored in the
Configuration tab; until there is data, conservative defaults are used.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from statistics import median

DEFAULT_COST = {"five_hour": 0.12, "seven_day": 0.025}   # conservative until measured
LEAN_FACTOR = 0.6                                        # lean plan ≈ 60 % of a full run
SAFETY = 1.15                                            # headroom for estimation error
WAIT_IF_RESET_WITHIN_MIN = 120
HARD_CEILING = 0.97                                      # never plan to use the last 3 %


def learned_cost(samples: list[dict]) -> dict:
    out = dict(DEFAULT_COST)
    for k in out:
        vals = [s[k] for s in samples[-7:] if isinstance(s.get(k), (int, float)) and 0 < s[k] < 1]
        if len(vals) >= 2:
            out[k] = max(median(vals), 0.005)
    return out


def plan(five_util: float, five_reset: int, seven_util: float, seven_reset: int,
         now: datetime | None = None, samples: list[dict] | None = None,
         five_status: str = "allowed", seven_status: str = "allowed") -> dict:
    now = now or datetime.now(timezone.utc)
    cost = learned_cost(samples or [])
    five_left = max(0.0, HARD_CEILING - five_util)
    seven_left = max(0.0, HARD_CEILING - seven_util)
    mins_to_five_reset = max(0.0, (five_reset - now.timestamp()) / 60)
    days_to_week_reset = max(1, math.ceil((seven_reset - now.timestamp()) / 86400))
    # Weekly pacing: today's video may use at most an equal share of what is left for the week.
    weekly_share = seven_left / days_to_week_reset
    full_w, lean_w = cost["seven_day"] * SAFETY, cost["seven_day"] * LEAN_FACTOR * SAFETY
    full_5, lean_5 = cost["five_hour"] * SAFETY, cost["five_hour"] * LEAN_FACTOR * SAFETY
    base = {"five_hour_left": round(five_left, 3), "seven_day_left": round(seven_left, 3),
            "days_to_weekly_reset": days_to_week_reset, "weekly_share_today": round(weekly_share, 4),
            "est_cost": {k: round(v, 4) for k, v in cost.items()},
            "weekly_resets_at": datetime.fromtimestamp(seven_reset, timezone.utc).isoformat(timespec="minutes"),
            "five_hour_resets_at": datetime.fromtimestamp(five_reset, timezone.utc).isoformat(timespec="minutes")}

    if seven_status != "allowed" or seven_left < lean_w:
        return base | {"decision": "weekly_stop",
                       "reason": "weekly Claude limit (almost) used up; no video until it resets"}
    mode = "full" if (weekly_share >= full_w and seven_left >= full_w) else "lean"
    need_5 = full_5 if mode == "full" else lean_5
    if five_status != "allowed" or five_left < need_5:
        if mode == "full" and five_left >= lean_5:
            return base | {"decision": "go_lean", "reason": "5-hour window is tight; lean plan fits"}
        if mins_to_five_reset <= WAIT_IF_RESET_WITHIN_MIN or five_status != "allowed":
            return base | {"decision": "wait", "resume_after_minutes": int(mins_to_five_reset) + 5,
                           "reason": "5-hour window nearly used; start right after it resets"}
        return base | {"decision": "go_lean", "reason": "5-hour window tight and reset is far; lean plan, checkpoint often"}
    if mode == "lean":
        return base | {"decision": "go_lean", "reason": "weekly pacing: keep one video for every remaining day"}
    return base | {"decision": "go_full", "reason": "enough budget"}


def record_sample(samples: list[dict], start: dict, end: dict) -> list[dict]:
    """Add one run's utilization delta (ignored when a window reset happened mid-run)."""
    s = {}
    for k in ("five_hour", "seven_day"):
        d = end.get(k, 0) - start.get(k, 0)
        if 0 < d < 1:
            s[k] = round(d, 4)
    return (samples + [s])[-14:] if s else samples


def loads(text: str) -> list[dict]:
    try:
        v = json.loads(text or "[]")
        return v if isinstance(v, list) else []
    except ValueError:
        return []
