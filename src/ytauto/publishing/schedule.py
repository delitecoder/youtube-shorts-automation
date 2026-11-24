"""Publication slot calculation (default 19:30 Asia/Kolkata)."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def next_slot(now: datetime, publish_time: str = "19:30", tz: str = "Asia/Kolkata",
              min_lead_minutes: int = 120) -> datetime:
    """First HH:MM in `tz` that is at least `min_lead_minutes` after `now`. Returns UTC."""
    zone = ZoneInfo(tz)
    hh, mm = (int(x) for x in publish_time.split(":"))
    local = now.astimezone(zone)
    cand = datetime.combine(local.date(), time(hh, mm), tzinfo=zone)
    while cand < local + timedelta(minutes=min_lead_minutes):
        cand += timedelta(days=1)
    return cand.astimezone(timezone.utc)


def free_slot(now: datetime, taken: set[str], publish_time: str = "19:30", tz: str = "Asia/Kolkata",
              min_lead_minutes: int = 120, approval_window_hours: int = 1,
              weekday_slots: dict | None = None) -> datetime:
    """Next peak slot that leaves time to approve and is not already used by another video.
    weekday_slots ({"mon": "19:30", ...}, publish tz) comes from analytics/peak.py; days without
    an entry use publish_time."""
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    zone = ZoneInfo(tz)
    earliest = (now + timedelta(hours=approval_window_hours, minutes=min_lead_minutes)).astimezone(zone)
    day = earliest.date()
    for _ in range(30):
        hhmm = (weekday_slots or {}).get(names[day.weekday()], publish_time)
        hh, mm = (int(x) for x in hhmm.split(":"))
        cand = datetime.combine(day, time(hh, mm), tzinfo=zone)
        if cand >= earliest and rfc3339(cand) not in taken:
            return cand.astimezone(timezone.utc)
        day += timedelta(days=1)
    raise RuntimeError("no free slot in the next 30 days")


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
