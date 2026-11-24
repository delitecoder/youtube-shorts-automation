"""Read-only Gmail lookup used by the publisher to re-verify an approval reply independently of
Apps Script (scope gmail.readonly). It fetches exactly one message by ID; it never lists or
searches the mailbox."""
from __future__ import annotations

import base64
from datetime import datetime, timezone

from ..approval.gate import ReplyEvidence


def _plain_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _plain_body(part)
        if text:
            return text
    return ""


def evidence_from_message(msg: dict) -> ReplyEvidence:
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    return ReplyEvidence(
        message_id=msg["id"],
        thread_id=msg["threadId"],
        sender=headers.get("from", ""),
        sent_at=datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc),
        body=_plain_body(msg["payload"]),
        label_ids=list(msg.get("labelIds", [])),
        auth_results=headers.get("authentication-results", ""),
    )


class GmailVerifier:
    def __init__(self, service):
        self.svc = service

    def fetch(self, message_id: str) -> ReplyEvidence:
        msg = self.svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        return evidence_from_message(msg)
