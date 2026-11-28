"""Workflow states, allowed transitions and the record stored per video.

UPLOADING is an addition to the requested state list: it is the lock state held while a
YouTube upload is in flight. Without it a crash mid-upload could not be told apart from
"approved, never uploaded", which risks a duplicate upload. A record stuck in UPLOADING is
only ever moved to SCHEDULED or NEEDS_MANUAL_REVIEW, never back to APPROVED.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class State(str, Enum):
    DISCOVERED = "DISCOVERED"
    RESEARCHING = "RESEARCHING"
    GENERATING = "GENERATING"
    QUALITY_CHECK = "QUALITY_CHECK"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    UPLOADING = "UPLOADING"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


S = State
TRANSITIONS: dict[State, frozenset[State]] = {
    S.DISCOVERED: frozenset({S.RESEARCHING, S.GENERATING, S.QUALITY_CHECK, S.NEEDS_MANUAL_REVIEW, S.FAILED}),
    S.RESEARCHING: frozenset({S.GENERATING, S.NEEDS_MANUAL_REVIEW, S.FAILED}),
    S.GENERATING: frozenset({S.QUALITY_CHECK, S.NEEDS_MANUAL_REVIEW, S.FAILED}),
    S.QUALITY_CHECK: frozenset({S.AWAITING_APPROVAL, S.GENERATING, S.NEEDS_MANUAL_REVIEW, S.FAILED}),
    S.AWAITING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.EXPIRED, S.FAILED}),
    S.APPROVED: frozenset({S.UPLOADING, S.NEEDS_MANUAL_REVIEW, S.EXPIRED}),
    S.UPLOADING: frozenset({S.SCHEDULED, S.NEEDS_MANUAL_REVIEW}),
    S.SCHEDULED: frozenset({S.PUBLISHED, S.NEEDS_MANUAL_REVIEW}),
    S.PUBLISHED: frozenset(),
    S.REJECTED: frozenset(),
    S.EXPIRED: frozenset({S.AWAITING_APPROVAL}),   # re-offered in a later approval email
    S.FAILED: frozenset(),
    S.NEEDS_MANUAL_REVIEW: frozenset(),
}
ACTIVE_STATES = frozenset({S.DISCOVERED, S.RESEARCHING, S.GENERATING, S.QUALITY_CHECK,
                           S.AWAITING_APPROVAL, S.APPROVED, S.UPLOADING})
TERMINAL_STATES = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)


class InvalidTransition(Exception):
    pass


class ConflictError(Exception):
    """The record changed underneath us (version/state mismatch)."""


def check_transition(current: State, new: State) -> None:
    if new not in TRANSITIONS[current]:
        raise InvalidTransition(f"{current.value} -> {new.value} is not allowed")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else ""


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {s}")
    return d


@dataclass
class VideoRecord:
    video_id: str
    state: State = State.DISCOVERED
    version: int = 1
    source_type: str = ""            # EXISTING | GENERATED
    drive_file_id: str = ""
    source_file_id: str = ""         # Drive id of the document/video that triggered the item
    file_name: str = ""
    sha256: str = ""
    duration_s: str = ""
    width: str = ""
    height: str = ""
    title: str = ""
    description: str = ""
    tags: str = "[]"                 # JSON list
    hook: str = ""
    pinned_comment: str = ""
    quality_report: str = ""         # JSON
    references: str = "[]"           # JSON list of sources
    preview_url: str = ""
    proposed_publish_at: str = ""
    approval_requested_at: str = ""
    approval_thread_id: str = ""
    approval_request_message_id: str = ""
    approval_expires_at: str = ""
    approval_message_id: str = ""
    approved_by: str = ""
    approved_at: str = ""
    webhook_status: str = ""
    youtube_video_id: str = ""
    published_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    write_token: str = ""
    notes: str = ""

    def to_row(self) -> dict[str, str]:
        d = dataclasses.asdict(self)
        d["state"] = self.state.value
        return {k: ("" if v is None else str(v)) for k, v in d.items()}

    @classmethod
    def from_row(cls, row: dict) -> "VideoRecord":
        kw = {f: row.get(f, "") for f in COLUMNS}
        kw["state"] = State(kw["state"] or "DISCOVERED")
        kw["version"] = int(kw["version"] or 1)
        return cls(**kw)

    @property
    def tag_list(self) -> list[str]:
        return json.loads(self.tags or "[]")


COLUMNS = tuple(f.name for f in dataclasses.fields(VideoRecord))
