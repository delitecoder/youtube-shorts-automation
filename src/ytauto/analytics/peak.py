"""Peak publish time per weekday, learned from the channel's own data (no model calls).

1. Audience location: top country by views (last 90 days) -> that country's time zone, so the
   evening peak is the viewers' evening, not just IST.
2. Own history: for every upload, views in the first 48 h (publish day + next day, Analytics
   `day` dimension), grouped by publish weekday and hour in the audience time zone.
3. Per weekday, pick the hour with the best median early views when it has >= MIN_PER_SLOT
   videos and beats the default evening slot by >= 15 %; otherwise use the default evening slot
   (19:30 audience time), which is the usual mobile peak.
The result is stored in Configuration as `publish_slots` (weekday -> HH:MM in the publish tz)
and refreshed weekly. Missing data never invents values; it falls back to the default.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

DEFAULT_LOCAL = "19:30"
MIN_PER_SLOT = 3
MIN_TOTAL = 12
MIN_MEDIAN_VIEWS = 20     # below this, early-view differences are noise
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
COUNTRY_TZ = {  # representative zone for the main audience country
    "IN": "Asia/Kolkata", "US": "America/New_York", "GB": "Europe/London", "CA": "America/Toronto",
    "AU": "Australia/Sydney", "PK": "Asia/Karachi", "BD": "Asia/Dhaka", "LK": "Asia/Colombo",
    "NP": "Asia/Kathmandu", "AE": "Asia/Dubai", "SG": "Asia/Singapore", "MY": "Asia/Kuala_Lumpur",
    "PH": "Asia/Manila", "ID": "Asia/Jakarta", "DE": "Europe/Berlin", "FR": "Europe/Paris",
    "NG": "Africa/Lagos", "KE": "Africa/Nairobi", "ZA": "Africa/Johannesburg", "BR": "America/Sao_Paulo",
}


def top_country(yta, today: date) -> tuple[str, float]:
    r = yta.reports().query(ids="channel==MINE", startDate=str(today - timedelta(days=90)), endDate=str(today),
                            metrics="views", dimensions="country", sort="-views", maxResults=5).execute()
    rows = r.get("rows") or []
    total = sum(x[1] for x in rows) or 1
    return (rows[0][0], rows[0][1] / total) if rows else ("IN", 0.0)


def early_views(yta, video_id: str, published: datetime) -> float | None:
    d0 = published.date()
    r = yta.reports().query(ids="channel==MINE", startDate=str(d0), endDate=str(d0 + timedelta(days=1)),
                            metrics="views", dimensions="day", filters=f"video=={video_id}").execute()
    rows = r.get("rows") or []
    return float(sum(x[1] for x in rows)) if rows else None


def choose_slots(samples: list[tuple[datetime, float]], audience_tz: str, publish_tz: str) -> dict:
    """samples: (published_at UTC, early views). Returns {weekday: 'HH:MM' in publish_tz, ...}."""
    a_zone, p_zone = ZoneInfo(audience_tz), ZoneInfo(publish_tz)
    by = defaultdict(list)
    for pub, v in samples:
        loc = pub.astimezone(a_zone)
        by[(loc.weekday(), loc.hour)].append(v)
    default_hour = int(DEFAULT_LOCAL[:2])
    slots, why = {}, {}
    for wd in range(7):
        cands = {h: median(vs) for (d, h), vs in by.items() if d == wd and len(vs) >= MIN_PER_SLOT}
        base = median(by[(wd, default_hour)]) if len(by[(wd, default_hour)]) >= MIN_PER_SLOT else None
        hour, minute = default_hour, int(DEFAULT_LOCAL[3:])
        reason = "default evening peak (not enough evidence for another hour yet)"
        if len(samples) >= MIN_TOTAL and cands and base is not None:
            best = max(cands, key=cands.get)
            if best != default_hour and cands[best] >= MIN_MEDIAN_VIEWS and cands[best] >= 1.15 * base:
                hour, minute, reason = best, 30 if best == default_hour else 0, f"best median early views ({cands[best]:.0f})"
        # express the audience-local slot in the publishing time zone
        ref = datetime(2026, 1, 5 + wd, hour, minute, tzinfo=a_zone)       # 2026-01-05 is a Monday
        slots[WEEKDAYS[wd]] = ref.astimezone(p_zone).strftime("%H:%M")
        why[WEEKDAYS[wd]] = reason
    return {"slots": slots, "why": why}


def compute(yt, yta, publish_tz: str = "Asia/Kolkata", today: date | None = None, max_videos: int = 60) -> dict:
    today = today or date.today()
    country, share = top_country(yta, today)
    audience_tz = COUNTRY_TZ.get(country, publish_tz)
    samples = []
    for v in yt.recent_uploads(limit=max_videos):
        pub = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
        if (today - pub.date()).days < 3:
            continue                                   # not enough time for a 48 h window
        ev = early_views(yta, v["id"], pub)
        if ev is not None:
            samples.append((pub, ev))
    res = choose_slots(samples, audience_tz, publish_tz)
    return res | {"audience_country": country, "audience_share": round(share, 2), "audience_tz": audience_tz,
                  "publish_tz": publish_tz, "videos_used": len(samples), "computed_on": str(today)}


def slot_for(slots_json: str, weekday: int, fallback: str) -> str:
    try:
        return json.loads(slots_json)["slots"][WEEKDAYS[weekday]]
    except (ValueError, KeyError, TypeError):
        return fallback
