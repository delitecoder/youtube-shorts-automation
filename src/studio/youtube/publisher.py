"""Publisher: the only component allowed to upload to YouTube.

It trusts nothing it is told. For each request it independently:
  1. loads the record and refuses anything not in APPROVED (idempotent no-op for items that
     are already UPLOADING/SCHEDULED/PUBLISHED or have a YouTube ID);
  2. finds the single accepted YES entry in ApprovalLog for this video and re-runs the full
     approval validation against it, and (optionally) against the message re-fetched from Gmail;
  3. checks the approval is still inside the publish window;
  4. downloads the exact Drive file and re-verifies its SHA-256;
  5. honours DRY_RUN / UPLOAD_ENABLED / PUBLICATION_ENABLED;
  6. moves APPROVED -> UPLOADING (compare-and-set) before touching YouTube, then
     UPLOADING -> SCHEDULED on success or -> NEEDS_MANUAL_REVIEW on any failure.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from studio.youtube.approval.gate import ApprovalRejected, ReplyEvidence, validate_reply
from studio.core.config import Settings
from studio.youtube.client import UploadRequest
from studio.core.state import ConflictError, State, iso, parse_iso, utcnow
from studio.storage.store import Store, log_error
from studio.youtube.schedule import next_slot, rfc3339


@dataclass
class PublishResult:
    status: str      # dry_run | disabled | duplicate | rejected | scheduled | uploaded_private | failed
    detail: str = ""
    youtube_video_id: str = ""

    def to_dict(self):
        return {"status": self.status, "detail": self.detail, "youtube_video_id": self.youtube_video_id}


def evidence_from_log(row: dict) -> ReplyEvidence:
    return ReplyEvidence(
        message_id=row["message_id"], thread_id=row["thread_id"], sender=row["sender"],
        sent_at=parse_iso(row["sent_at"]), body=row["body_excerpt"],
        label_ids=[x for x in row.get("label_ids", "").split(",") if x],
        auth_results=row.get("auth_results", ""))


class Publisher:
    def __init__(self, settings: Settings, store: Store, drive, youtube, gmail=None,
                 clock=utcnow, workdir: Path | None = None):
        self.s, self.store, self.drive, self.yt, self.gmail = settings, store, drive, youtube, gmail
        self.clock = clock
        self.workdir = workdir

    # ------------------------------------------------------------------
    def _accepted_yes(self, video_id: str) -> tuple[dict, set[str]]:
        rows = [r for r in self.store.rows("ApprovalLog") if r.get("video_id") == video_id]
        yes = [r for r in rows if r.get("accepted") == "true" and r.get("command") == f"YES {video_id}"]
        if len(yes) != 1:
            raise ApprovalRejected(f"expected exactly one accepted YES in ApprovalLog, found {len(yes)}")
        others = {r["message_id"] for r in rows if r is not yes[0] and r.get("accepted") == "true"}
        return yes[0], others

    def _verify_approval(self, rec) -> None:
        row, other_used = self._accepted_yes(rec.video_id)
        if row["message_id"] != rec.approval_message_id:
            raise ApprovalRejected("ApprovalLog message does not match the record's approval message")
        if row["sender"] and rec.approved_by and rec.approved_by.lower() not in row["sender"].lower():
            raise ApprovalRejected("approved_by does not match the logged sender")
        kwargs = dict(approver=self.s.approver_email, approver_is_owner=self.s.approver_is_owner,
                      ttl_hours=self.s.approval_ttl_hours, used_message_ids=other_used,
                      expected_state=State.APPROVED)
        logged = evidence_from_log(row)
        # Evaluate as of the reply time: it must have been valid when it was received.
        if validate_reply(rec, logged, now=logged.sent_at, **kwargs) != "YES":
            raise ApprovalRejected("logged decision is not YES")
        if self.s.publisher_verify_gmail:
            if self.gmail is None:
                raise ApprovalRejected("Gmail re-verification required but not configured")
            live = self.gmail.fetch(rec.approval_message_id)
            if validate_reply(rec, live, now=live.sent_at, **kwargs) != "YES":
                raise ApprovalRejected("Gmail message is not a YES")
            if live.thread_id != logged.thread_id or abs((live.sent_at - logged.sent_at).total_seconds()) > 5:
                raise ApprovalRejected("Gmail message does not match ApprovalLog")
        approved_at = parse_iso(rec.approved_at) or logged.sent_at
        if self.clock() > approved_at + timedelta(hours=self.s.publish_window_hours):
            raise ApprovalRejected("approval is older than the publish window; re-approval needed")

    def _fail(self, rec, new_state: State, msg: str) -> PublishResult:
        log_error(self.store, "publisher", msg, rec.video_id)
        try:
            self.store.transition(rec.video_id, rec.state, new_state, rec.version, notes=msg[:500])
        except Exception as e:  # keep original error visible
            log_error(self.store, "publisher", f"could not set {new_state.value}: {e}", rec.video_id)
        return PublishResult("failed", msg)

    # ------------------------------------------------------------------
    def publish(self, video_id: str) -> PublishResult:
        rec = self.store.get(video_id)
        if rec is None:
            return PublishResult("rejected", "unknown video id")
        if rec.youtube_video_id or rec.state in (State.UPLOADING, State.SCHEDULED, State.PUBLISHED):
            return PublishResult("duplicate", f"already {rec.state.value}", rec.youtube_video_id)
        if rec.state != State.APPROVED:
            return PublishResult("rejected", f"state is {rec.state.value}, not APPROVED")
        try:
            self._verify_approval(rec)
        except ApprovalRejected as e:
            log_error(self.store, "publisher", f"approval verification failed: {e.reason}", video_id)
            return PublishResult("rejected", e.reason)

        tmp = Path(tempfile.mkdtemp(dir=self.workdir))
        try:
            local, digest = self.drive.download(rec.drive_file_id, tmp / (rec.file_name or "video.mp4"))
            if digest != rec.sha256:
                return self._fail(rec, State.NEEDS_MANUAL_REVIEW,
                                  f"checksum mismatch: approved {rec.sha256[:12]}…, Drive now {digest[:12]}…")
            if self.s.dry_run or not self.s.upload_enabled:
                mode = "dry_run" if self.s.dry_run else "disabled"
                log_error(self.store, "publisher", f"{mode}: verified, upload skipped", video_id, "INFO")
                return PublishResult(mode, "approval and checksum verified; upload skipped by flags")

            publish_at = None
            if self.s.publication_enabled:
                proposed = parse_iso(rec.proposed_publish_at)
                earliest = self.clock() + timedelta(minutes=self.s.min_lead_minutes)
                slot = proposed if proposed and proposed >= earliest else next_slot(
                    self.clock(), self.s.publish_time, self.s.timezone, self.s.min_lead_minutes)
                publish_at = rfc3339(slot)

            try:
                rec = self.store.transition(video_id, State.APPROVED, State.UPLOADING, rec.version)
            except ConflictError as e:
                return PublishResult("duplicate", f"lost race: {e}")

            try:
                resp = self.yt.upload(UploadRequest(
                    path=local, title=rec.title, description=rec.description, tags=rec.tag_list,
                    category_id=self.s.youtube_category_id, publish_at=publish_at,
                    language=self.s.channel_language))
            except Exception as e:  # unknown whether YouTube created the video -> human check
                return self._fail(rec, State.NEEDS_MANUAL_REVIEW, f"upload failed: {e}")

            yt_id = resp["id"]
            rec = self.store.transition(video_id, State.UPLOADING, State.SCHEDULED, rec.version,
                                        youtube_video_id=yt_id, proposed_publish_at=publish_at or "")
            self.store.append("PublishedVideos", {
                "recorded_at": iso(self.clock()), "video_id": video_id, "youtube_video_id": yt_id,
                "privacy": "private", "publish_at": publish_at or "", "title": rec.title,
                "sha256": rec.sha256, "status": "scheduled" if publish_at else "uploaded_private"})
            return PublishResult("scheduled" if publish_at else "uploaded_private",
                                 publish_at or "private, no publish time (PUBLICATION_ENABLED=false)", yt_id)
        except Exception as e:
            log_error(self.store, "publisher", f"unexpected error: {e}", video_id)
            return PublishResult("failed", str(e))
        finally:
            for p in tmp.glob("*"):
                p.unlink(missing_ok=True)
            tmp.rmdir()
