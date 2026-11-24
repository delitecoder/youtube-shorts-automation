from datetime import datetime, timezone

import pytest

from ytauto.state import ConflictError, InvalidTransition, State, TERMINAL_STATES, VideoRecord
from ytauto.store import next_video_id


def test_happy_path_transitions(store):
    rec = store.create(VideoRecord(video_id="VIDEO-20261124-001"))
    rec = store.transition(rec.video_id, State.DISCOVERED, State.RESEARCHING, rec.version)
    assert rec.state == State.RESEARCHING and rec.version == 2


def test_invalid_transition(store):
    rec = store.create(VideoRecord(video_id="VIDEO-20261124-001"))
    with pytest.raises(InvalidTransition):
        store.transition(rec.video_id, State.DISCOVERED, State.APPROVED, rec.version)


def test_stale_version_conflict(store):
    rec = store.create(VideoRecord(video_id="VIDEO-20261124-001"))
    store.transition(rec.video_id, State.DISCOVERED, State.RESEARCHING, rec.version)
    with pytest.raises(ConflictError):
        store.transition(rec.video_id, State.RESEARCHING, State.GENERATING, rec.version)  # stale


def test_wrong_expected_state_conflict(store):
    rec = store.create(VideoRecord(video_id="VIDEO-20261124-001"))
    with pytest.raises(ConflictError):
        store.transition(rec.video_id, State.RESEARCHING, State.GENERATING, rec.version)


def test_duplicate_create_rejected(store):
    store.create(VideoRecord(video_id="VIDEO-20261124-001"))
    with pytest.raises(ConflictError):
        store.create(VideoRecord(video_id="VIDEO-20261124-001"))


def test_protected_fields_not_updatable(store):
    rec = store.create(VideoRecord(video_id="VIDEO-20261124-001"))
    with pytest.raises(ValueError):
        store.update_fields(rec.video_id, rec.version, state="APPROVED")


def test_terminal_states_have_no_exits():
    assert {State.PUBLISHED, State.REJECTED, State.EXPIRED, State.FAILED,
            State.NEEDS_MANUAL_REVIEW} == set(TERMINAL_STATES)


def test_approval_cannot_be_skipped():
    from ytauto.state import TRANSITIONS
    into_approved = [s for s, nxt in TRANSITIONS.items() if State.APPROVED in nxt]
    into_uploading = [s for s, nxt in TRANSITIONS.items() if State.UPLOADING in nxt]
    assert into_approved == [State.AWAITING_APPROVAL]
    assert into_uploading == [State.APPROVED]


def test_video_id_sequence_uses_ist_date():
    now = datetime(2026, 11, 24, 20, 0, tzinfo=timezone.utc)   # already 25 Nov in IST
    assert next_video_id([], now) == "VIDEO-20261125-001"
    assert next_video_id(["VIDEO-20261125-001", "VIDEO-20261125-007", "VIDEO-20261124-009"], now) \
        == "VIDEO-20261125-008"
