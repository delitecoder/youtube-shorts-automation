import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ytauto.config import Settings  # noqa: E402
from ytauto.state import State, VideoRecord, iso  # noqa: E402
from ytauto.store import JsonFileStore  # noqa: E402

APPROVER = "owner@example.com"
T0 = datetime(2026, 11, 24, 5, 0, tzinfo=timezone.utc)   # approval request sent


@pytest.fixture
def settings():
    return Settings(dry_run=False, upload_enabled=True, publication_enabled=True,
                    approver_email=APPROVER, approver_is_owner=True,
                    folders={"awaiting_approval": "f3", "generated": "f2"})


@pytest.fixture
def store(tmp_path):
    return JsonFileStore(tmp_path / "state.json")


def awaiting_record(store, video_id="VIDEO-20261124-001", sha="ab" * 32) -> VideoRecord:
    rec = store.create(VideoRecord(video_id=video_id, source_type="GENERATED"))
    for a, b in ((State.DISCOVERED, State.RESEARCHING), (State.RESEARCHING, State.GENERATING),
                 (State.GENERATING, State.QUALITY_CHECK)):
        rec = store.transition(video_id, a, b, rec.version)
    return store.transition(
        video_id, State.QUALITY_CHECK, State.AWAITING_APPROVAL, rec.version,
        title="How Hash Tables Work", description="desc #Shorts", tags='["hash"]',
        drive_file_id="file123", file_name="v.mp4", sha256=sha,
        approval_requested_at=iso(T0), approval_thread_id="thread-1",
        approval_request_message_id="msg-request",
        proposed_publish_at=iso(T0 + timedelta(hours=9)))


@pytest.fixture
def t0():
    return T0
