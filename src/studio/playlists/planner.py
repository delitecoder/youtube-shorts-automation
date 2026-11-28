"""Playlist planning (deterministic; the pipeline stays read-only on YouTube).

plan() maps a video to its subject playlist key (+language), decides whether the playlist may be created
(>= min_videos submitted in that subject+language) and lists earlier uploaded videos of the subject to add
too (backfill). The approved snapshot carries the plan; the owner-deployed Apps Script executes it after the
upload with idempotent find-by-marker / add-once logic. No playlist is ever made for a single video.
"""
from __future__ import annotations

from studio.core import conf
from studio.storage.store import language_of


def subject_for(category: str, cfg: dict | None = None) -> str | None:
    c = cfg or conf.load("playlists")
    cat = (category or "").lower()
    for key, s in c["subjects"].items():
        if cat in [x.lower() for x in s["categories"]]:
            return key
    return None


def plan(store, video_id: str, category: str, cfg: dict | None = None) -> dict | None:
    c = cfg or conf.load("playlists")
    key = subject_for(category, c)
    if not key:
        return None
    lang = language_of(video_id)
    subj = c["subjects"][key]
    peers = [m["video_id"] for m in store.rows("ProductionManifest")
             if m.get("video_id") != video_id and m.get("lang") == lang and subject_for(m.get("topic_category", ""), c) == key]
    published = {p["video_id"]: p.get("youtube_video_id") for p in store.rows("PublishedVideos") if p.get("youtube_video_id")}
    return {"key": f"{key}-{lang}", "title": subj["title"][lang], "description": subj["description"][lang], "lang": lang,
            "create": len(peers) + 1 >= c.get("min_videos", 2),
            "backfill": [published[v] for v in peers if v in published]}
