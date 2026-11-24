"""Workflow database.

`Store` is the interface. Implementations:
  * JsonFileStore  – local file with an OS file lock; used for DRY_RUN and tests.
  * SheetsStore    – Google Sheets spreadsheet "YouTube_Automation_Control" (see integrations/sheets.py).

Every state change goes through `transition()`, which is a compare-and-set on
(state, version). Sheets has no native compare-and-set, so SheetsStore additionally writes a
random `write_token` and re-reads the row to detect a lost race; in addition each transition
has exactly one owning component (see docs/security.md), so concurrent writers to the same
transition do not exist in normal operation.
"""
from __future__ import annotations

import fcntl
import json
import re
import secrets
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol
from zoneinfo import ZoneInfo

from .state import (COLUMNS, ConflictError, State, VideoRecord, check_transition, iso, utcnow)

TABS: dict[str, tuple[str, ...]] = {
    "VideoQueue": COLUMNS,
    "ResearchHistory": ("researched_at", "video_id", "topic", "topic_key", "category",
                        "sources", "summary"),
    "PublishedVideos": ("recorded_at", "video_id", "youtube_video_id", "privacy", "publish_at",
                        "title", "sha256", "status"),
    "ApprovalLog": ("logged_at", "video_id", "message_id", "thread_id", "sender", "sent_at",
                    "label_ids", "auth_results", "body_excerpt", "command", "accepted", "reason",
                    "transition"),
    "Analytics": ("snapshot_at", "video_id", "youtube_video_id", "published_at", "window_start",
                  "window_end", "views", "engagedViews", "estimatedMinutesWatched",
                  "averageViewDuration", "averageViewPercentage", "likes", "comments", "shares",
                  "subscribersGained", "raw"),
    "Configuration": ("key", "value", "updated_at"),
    "ErrorLog": ("logged_at", "video_id", "component", "severity", "message", "notified"),
}

VIDEO_ID_RE = re.compile(r"^VIDEO-(\d{8})-(\d{3})$")


class Store(Protocol):
    def get(self, video_id: str) -> VideoRecord | None: ...
    def all(self) -> list[VideoRecord]: ...
    def create(self, rec: VideoRecord) -> VideoRecord: ...
    def transition(self, video_id: str, expected: State, new: State, expected_version: int,
                   **updates: str) -> VideoRecord: ...
    def update_fields(self, video_id: str, expected_version: int, **updates: str) -> VideoRecord: ...
    def append(self, tab: str, row: dict) -> None: ...
    def rows(self, tab: str) -> list[dict]: ...
    def get_config(self, key: str, default: str = "") -> str: ...
    def set_config(self, key: str, value: str) -> None: ...


# ------------------------------------------------------------------ shared helpers
def next_video_id(existing: Iterable[str], now: datetime, tz: str = "Asia/Kolkata") -> str:
    day = now.astimezone(ZoneInfo(tz)).strftime("%Y%m%d")
    nums = [int(m.group(2)) for v in existing if (m := VIDEO_ID_RE.match(v)) and m.group(1) == day]
    n = max(nums, default=0) + 1
    if n > 999:
        raise RuntimeError("video id space exhausted for today")
    return f"VIDEO-{day}-{n:03d}"


def log_error(store: Store, component: str, message: str, video_id: str = "",
              severity: str = "ERROR") -> None:
    store.append("ErrorLog", {"logged_at": iso(utcnow()), "video_id": video_id,
                              "component": component, "severity": severity,
                              "message": message[:2000], "notified": ""})


def _apply(rec: VideoRecord, expected: State | None, new: State | None,
           expected_version: int, updates: dict) -> VideoRecord:
    if rec.version != expected_version:
        raise ConflictError(f"{rec.video_id}: version {rec.version} != expected {expected_version}")
    if expected is not None and rec.state != expected:
        raise ConflictError(f"{rec.video_id}: state {rec.state.value} != expected {expected.value}")
    if new is not None:
        check_transition(rec.state, new)
    bad = set(updates) - set(COLUMNS)
    protected = {"video_id", "state", "version", "created_at", "write_token"} & set(updates)
    if bad or protected:
        raise ValueError(f"cannot update fields: {sorted(bad | protected)}")
    row = rec.to_row()
    row.update({k: str(v) for k, v in updates.items()})
    if new is not None:
        row["state"] = new.value
    row["version"] = str(rec.version + 1)
    row["updated_at"] = iso(utcnow())
    row["write_token"] = secrets.token_hex(8)
    return VideoRecord.from_row(row)


# ------------------------------------------------------------------ JSON file store
class JsonFileStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({t: [] for t in TABS}))

    @contextmanager
    def _locked(self, write: bool):
        with open(self.path, "r+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            data = json.loads(fh.read() or "{}")
            for t in TABS:
                data.setdefault(t, [])
            yield data
            if write:
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(data, indent=1))
            fcntl.flock(fh, fcntl.LOCK_UN)

    def get(self, video_id):
        with self._locked(False) as d:
            for r in d["VideoQueue"]:
                if r["video_id"] == video_id:
                    return VideoRecord.from_row(r)
        return None

    def all(self):
        with self._locked(False) as d:
            return [VideoRecord.from_row(r) for r in d["VideoQueue"]]

    def create(self, rec):
        with self._locked(True) as d:
            if any(r["video_id"] == rec.video_id for r in d["VideoQueue"]):
                raise ConflictError(f"{rec.video_id} already exists")
            now = iso(utcnow())
            rec.created_at = rec.created_at or now
            rec.updated_at = now
            rec.write_token = secrets.token_hex(8)
            d["VideoQueue"].append(rec.to_row())
        return rec

    def _mutate(self, video_id, expected, new, expected_version, updates):
        with self._locked(True) as d:
            for i, r in enumerate(d["VideoQueue"]):
                if r["video_id"] == video_id:
                    out = _apply(VideoRecord.from_row(r), expected, new, expected_version, updates)
                    d["VideoQueue"][i] = out.to_row()
                    return out
        raise KeyError(video_id)

    def transition(self, video_id, expected, new, expected_version, **updates):
        return self._mutate(video_id, expected, new, expected_version, updates)

    def update_fields(self, video_id, expected_version, **updates):
        return self._mutate(video_id, None, None, expected_version, updates)

    def append(self, tab, row):
        if tab not in TABS or tab == "VideoQueue":
            raise ValueError(tab)
        with self._locked(True) as d:
            d[tab].append({c: str(row.get(c, "")) for c in TABS[tab]})

    def rows(self, tab):
        with self._locked(False) as d:
            return [dict(r) for r in d[tab]]

    def get_config(self, key, default=""):
        val = default
        for r in self.rows("Configuration"):
            if r["key"] == key:
                val = r["value"]  # last write wins
        return val

    def set_config(self, key, value):
        self.append("Configuration", {"key": key, "value": value, "updated_at": iso(utcnow())})
