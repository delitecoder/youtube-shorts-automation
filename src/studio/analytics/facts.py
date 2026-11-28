"""VideoFacts: normalised per-video metrics for learning. A metric the API did not return is UNKNOWN
(empty cell + listed in unknown_metrics), never zero.

Source: the Analytics tab written by `studio analytics --snapshot` (YouTube Analytics API v2 per-video
metrics). Impressions and CTR are not available per video through the Analytics API for this channel
type (they live in YouTube Studio / bulk Reporting API), so they stay unknown unless a source provides them.
"""
from __future__ import annotations

from datetime import datetime

from studio.core.state import iso, utcnow
from studio.storage.store import is_long, language_of

FIELDS = {"views": "views", "avg_view_pct": "averageViewPercentage", "avg_view_s": "averageViewDuration",
          "watch_min": "estimatedMinutesWatched", "likes": "likes", "comments": "comments", "shares": "shares",
          "subs": "subscribersGained", "impressions": "impressions", "ctr": "impressionClickThroughRate"}


def _num(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def from_snapshot(row: dict, now: datetime | None = None) -> dict:
    vid = row.get("video_id", "")
    out = {"snapshot_at": iso(now or utcnow()), "video_id": vid, "youtube_video_id": row.get("youtube_video_id", ""),
           "format": "long" if vid and is_long(vid) else "short", "lang": language_of(vid) if vid else "",
           "days_live": ""}
    unknown = []
    for f, src in FIELDS.items():
        v = _num(row.get(src))
        out[f] = "" if v is None else (f"{v:g}")
        if v is None:
            unknown.append(f)
    pub = row.get("published_at") or ""
    if pub:
        try:
            d = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            out["days_live"] = str(max(0, ((now or utcnow()) - d).days))
        except ValueError:
            pass
    out["unknown_metrics"] = ",".join(unknown)
    return out


def latest(store) -> dict[str, dict]:
    """Newest VideoFacts row per video."""
    best: dict[str, dict] = {}
    for r in store.rows("VideoFacts"):
        if r.get("video_id") and r.get("snapshot_at", "") >= best.get(r["video_id"], {}).get("snapshot_at", ""):
            best[r["video_id"]] = r
    return best


def refresh(store, now: datetime | None = None) -> int:
    """Convert the newest Analytics snapshot per video into a VideoFacts row."""
    snaps: dict[str, dict] = {}
    for r in store.rows("Analytics"):
        if r.get("video_id") and r.get("snapshot_at", "") >= snaps.get(r["video_id"], {}).get("snapshot_at", ""):
            snaps[r["video_id"]] = r
    for r in snaps.values():
        store.append("VideoFacts", from_snapshot(r, now))
    return len(snaps)


def metric(fact: dict, name: str) -> float | None:
    return _num(fact.get(name))
