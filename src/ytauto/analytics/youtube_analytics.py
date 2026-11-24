"""YouTube Analytics snapshots and the compact performance summary fed to the storywriter.

Only metrics the API returns are stored; missing values stay blank (never estimated).
Snapshots are appended (history is never rewritten).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from ..state import iso, parse_iso, utcnow

METRICS = ("views,engagedViews,estimatedMinutesWatched,averageViewDuration,"
           "averageViewPercentage,likes,comments,shares,subscribersGained")
MIN_VIDEOS_FOR_TIME_OPTIMISATION = 15
MIN_SLOTS = 3


def query_video(yta, youtube_video_id: str, start: date, end: date) -> dict:
    """Try the full metric set; if the API rejects a metric (availability changes over time),
    fall back to the stable core set. Never fabricates values."""
    for metrics in (METRICS, "views,estimatedMinutesWatched,averageViewDuration,"
                             "averageViewPercentage,likes,comments,shares,subscribersGained"):
        try:
            r = yta.reports().query(ids="channel==MINE", startDate=start.isoformat(),
                                    endDate=end.isoformat(), metrics=metrics,
                                    filters=f"video=={youtube_video_id}").execute()
        except Exception as e:  # noqa: BLE001 - retry with the smaller metric set
            last = e
            continue
        rows = r.get("rows") or []
        if not rows:
            return {}
        return dict(zip([h["name"] for h in r["columnHeaders"]], rows[0]))
    raise last


def snapshot(store, yta, published_rows: list[dict], now=None) -> int:
    now = now or utcnow()
    n = 0
    for p in published_rows:
        pub = parse_iso(p.get("published_at") or p.get("publish_at") or "")
        if not pub or not p.get("youtube_video_id"):
            continue
        start, end = pub.date(), now.date()
        m = query_video(yta, p["youtube_video_id"], start, end)
        row = {"snapshot_at": iso(now), "video_id": p.get("video_id", ""),
               "youtube_video_id": p["youtube_video_id"], "published_at": iso(pub),
               "window_start": start.isoformat(), "window_end": end.isoformat(),
               "raw": json.dumps(m)}
        row.update({k: m.get(k, "") for k in METRICS.split(",")})
        store.append("Analytics", row)
        n += 1
    return n


def latest_by_video(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        k = r["youtube_video_id"]
        if k not in out or r["snapshot_at"] > out[k]["snapshot_at"]:
            out[k] = r
    return out


def summary(rows: list[dict], titles: dict[str, str], top_n: int = 3) -> dict:
    """Compact JSON for the storywriter: best/worst performers vs. channel median."""
    latest = [r for r in latest_by_video(rows).values() if r.get("views") not in ("", None)]
    if not latest:
        return {"videos_with_data": 0, "note": "no analytics yet; use general best practice"}

    def f(r, k):
        try:
            return float(r.get(k) or 0)
        except ValueError:
            return 0.0

    med = {k: median(f(r, k) for r in latest) for k in ("views", "averageViewPercentage", "likes")}
    ranked = sorted(latest, key=lambda r: (f(r, "averageViewPercentage"), f(r, "views")), reverse=True)

    def brief(r):
        return {"title": titles.get(r["youtube_video_id"], ""), "views": r.get("views"),
                "engagedViews": r.get("engagedViews"), "avgViewPct": r.get("averageViewPercentage"),
                "avgViewDurationS": r.get("averageViewDuration"), "likes": r.get("likes"),
                "comments": r.get("comments"), "subsGained": r.get("subscribersGained")}

    return {"videos_with_data": len(latest), "channel_median": med,
            "best": [brief(r) for r in ranked[:top_n]],
            "weakest": [brief(r) for r in ranked[-top_n:]] if len(ranked) > top_n else []}


def recommend_publish_time(rows: list[dict], tz: str, default: str = "19:30") -> dict:
    """Compare first-week views by local publish time across own videos. Only recommends a change
    with enough data (>= MIN_VIDEOS_FOR_TIME_OPTIMISATION videos across >= MIN_SLOTS slots)."""
    latest = latest_by_video(rows).values()
    by_slot: dict[str, list[float]] = {}
    for r in latest:
        pub = parse_iso(r.get("published_at"))
        if not pub or r.get("views") in ("", None):
            continue
        slot = pub.astimezone(ZoneInfo(tz)).strftime("%H:00")
        by_slot.setdefault(slot, []).append(float(r["views"]))
    total = sum(len(v) for v in by_slot.values())
    if total < MIN_VIDEOS_FOR_TIME_OPTIMISATION or len(by_slot) < MIN_SLOTS:
        return {"publish_time": default, "changed": False,
                "reason": f"insufficient data ({total} videos, {len(by_slot)} slots)"}
    best = max(by_slot, key=lambda s: median(by_slot[s]))
    default_slot = default[:2] + ":00"
    if best == default_slot or median(by_slot[best]) < 1.2 * median(by_slot.get(default_slot, [0])):
        return {"publish_time": default, "changed": False, "reason": "no slot beats current by 20%",
                "medians": {s: median(v) for s, v in by_slot.items()}}
    return {"publish_time": best, "changed": True,
            "medians": {s: median(v) for s, v in by_slot.items()},
            "reason": "median cumulative views by publish hour; requires your approval to apply"}
