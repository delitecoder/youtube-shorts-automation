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
              min_lead_minutes: int = 120, approval_window_hours: int = 1) -> datetime:
    """Next evening slot that leaves time to approve and is not already used by another video
    (a video finished late after a usage-limit pause must not collide with tomorrow's video)."""
    slot = next_slot(now + timedelta(hours=approval_window_hours), publish_time, tz, min_lead_minutes)
    while rfc3339(slot) in taken:
        slot += timedelta(days=1)
    return slot


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
