"""Publisher: approval re-verification, checksum, flags, idempotency, failure handling (all mocked)."""
import dataclasses
import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import APPROVER, awaiting_record

from ytauto.approval.gate import ReplyEvidence
from ytauto.publishing.publisher import Publisher
from ytauto.state import State, iso

VID = "VIDEO-20261124-001"
CONTENT = b"exact approved bytes"
SHA = hashlib.sha256(CONTENT).hexdigest()


class FakeDrive:
    def __init__(self, content=CONTENT):
        self.content = content

    def download(self, file_id, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.content)
        return dest, hashlib.sha256(self.content).hexdigest()


class FakeYouTube:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def upload(self, req):
        self.calls.append(req)
        assert req.path.read_bytes() == CONTENT          # exact approved file
        if self.fail:
            raise RuntimeError("connection reset after upload started")
        return {"id": "yt123"}


class FakeGmail:
    def __init__(self, evidence):
        self.ev = evidence

    def fetch(self, message_id):
        return self.ev


def approve(store, t0, minutes=10, body=f"YES {VID}", sender=f"Me <{APPROVER}>", log_body=None):
    rec = awaiting_record(store, sha=SHA)
    sent = t0 + timedelta(minutes=minutes)
    store.append("ApprovalLog", {
        "logged_at": iso(sent), "video_id": VID, "message_id": "msg-yes", "thread_id": "thread-1",
        "sender": sender, "sent_at": iso(sent), "label_ids": "SENT", "auth_results": "",
        "body_excerpt": log_body or body, "command": f"YES {VID}", "accepted": "true", "reason": "",
        "transition": "AWAITING_APPROVAL->APPROVED"})
    store.transition(VID, State.AWAITING_APPROVAL, State.APPROVED, rec.version,
                     approval_message_id="msg-yes", approved_by=APPROVER, approved_at=iso(sent))
    return ReplyEvidence("msg-yes", "thread-1", sender, sent, body, ["SENT"], "")


def make(settings, store, t0, yt=None, drive=None, gmail_ev=None, now_h=1, **flags):
    s = dataclasses.replace(settings, **flags)
    return Publisher(s, store, drive or FakeDrive(), yt or FakeYouTube(),
                     FakeGmail(gmail_ev) if gmail_ev else None, clock=lambda: t0 + timedelta(hours=now_h))


def test_happy_path_uploads_private_scheduled(settings, store, t0):
    ev = approve(store, t0)
    yt = FakeYouTube()
    r = make(settings, store, t0, yt=yt, gmail_ev=ev).publish(VID)
    assert r.status == "scheduled" and r.youtube_video_id == "yt123"
    req = yt.calls[0]
    assert req.publish_at and req.publish_at.endswith("Z")
    rec = store.get(VID)
    assert rec.state == State.SCHEDULED and rec.youtube_video_id == "yt123"
    assert store.rows("PublishedVideos")[0]["youtube_video_id"] == "yt123"


def test_duplicate_call_never_uploads_twice(settings, store, t0):
    ev = approve(store, t0)
    yt = FakeYouTube()
    p = make(settings, store, t0, yt=yt, gmail_ev=ev)
    assert p.publish(VID).status == "scheduled"
    assert p.publish(VID).status == "duplicate"
    assert len(yt.calls) == 1


def test_dry_run_verifies_but_does_not_upload(settings, store, t0):
    ev = approve(store, t0)
    yt = FakeYouTube()
    r = make(settings, store, t0, yt=yt, gmail_ev=ev, dry_run=True).publish(VID)
    assert r.status == "dry_run" and not yt.calls and store.get(VID).state == State.APPROVED


def test_upload_disabled(settings, store, t0):
    ev = approve(store, t0)
    yt = FakeYouTube()
    assert make(settings, store, t0, yt=yt, gmail_ev=ev, upload_enabled=False).publish(VID).status == "disabled"
    assert not yt.calls


def test_publication_disabled_stays_private_without_schedule(settings, store, t0):
    ev = approve(store, t0)
    yt = FakeYouTube()
    r = make(settings, store, t0, yt=yt, gmail_ev=ev, publication_enabled=False).publish(VID)
    assert r.status == "uploaded_private" and yt.calls[0].publish_at is None


def test_checksum_mismatch_goes_to_manual_review(settings, store, t0):
    ev = approve(store, t0)
    yt = FakeYouTube()
    r = make(settings, store, t0, yt=yt, drive=FakeDrive(b"tampered"), gmail_ev=ev).publish(VID)
    assert r.status == "failed" and not yt.calls
    assert store.get(VID).state == State.NEEDS_MANUAL_REVIEW


def test_upload_failure_is_safe_and_not_retried_blindly(settings, store, t0):
    ev = approve(store, t0)
    yt = FakeYouTube(fail=True)
    p = make(settings, store, t0, yt=yt, gmail_ev=ev)
    assert p.publish(VID).status == "failed"
    assert store.get(VID).state == State.NEEDS_MANUAL_REVIEW
    assert p.publish(VID).status == "rejected"        # a retry cannot upload again
    assert len(yt.calls) == 1
    assert any("upload failed" in e["message"] for e in store.rows("ErrorLog"))


def test_no_approval_record_rejected(settings, store, t0):
    awaiting_record(store, sha=SHA)
    r = make(settings, store, t0).publish(VID)
    assert r.status == "rejected" and "APPROVED" in r.detail


def test_approved_state_without_ledger_entry_rejected(settings, store, t0):
    rec = awaiting_record(store, sha=SHA)
    store.transition(VID, State.AWAITING_APPROVAL, State.APPROVED, rec.version,
                     approval_message_id="msg-yes", approved_by=APPROVER, approved_at=iso(t0))
    yt = FakeYouTube()
    r = make(settings, store, t0, yt=yt, publisher_verify_gmail=False).publish(VID)
    assert r.status == "rejected" and "ApprovalLog" in r.detail and not yt.calls


def test_tampered_ledger_sender_rejected(settings, store, t0):
    ev = approve(store, t0, sender="Mallory <mallory@example.com>")
    yt = FakeYouTube()
    r = make(settings, store, t0, yt=yt, gmail_ev=ev).publish(VID)
    assert r.status == "rejected" and not yt.calls


def test_ledger_with_quoted_only_approval_rejected(settings, store, t0):
    ev = approve(store, t0, log_body=f"ok\n> YES {VID}")
    yt = FakeYouTube()
    r = make(settings, store, t0, yt=yt, gmail_ev=ev).publish(VID)
    assert r.status == "rejected" and not yt.calls


def test_gmail_reverification_mismatch_rejected(settings, store, t0):
    ev = approve(store, t0)
    live = dataclasses.replace(ev, body=f"NO {VID}")
    yt = FakeYouTube()
    assert make(settings, store, t0, yt=yt, gmail_ev=live).publish(VID).status == "rejected"
    assert not yt.calls


def test_gmail_required_but_missing_rejected(settings, store, t0):
    approve(store, t0)
    yt = FakeYouTube()
    assert make(settings, store, t0, yt=yt).publish(VID).status == "rejected"
    assert not yt.calls


def test_stale_approval_outside_publish_window_rejected(settings, store, t0):
    ev = approve(store, t0)
    yt = FakeYouTube()
    r = make(settings, store, t0, yt=yt, gmail_ev=ev, now_h=30).publish(VID)
    assert r.status == "rejected" and "window" in r.detail and not yt.calls


def test_two_accepted_yes_entries_rejected(settings, store, t0):
    ev = approve(store, t0)
    row = store.rows("ApprovalLog")[0] | {"message_id": "msg-yes-2"}
    store.append("ApprovalLog", row)
    assert make(settings, store, t0, gmail_ev=ev).publish(VID).status == "rejected"


def test_unknown_video_rejected(settings, store, t0):
    assert make(settings, store, t0).publish("VIDEO-20261124-099").status == "rejected"
