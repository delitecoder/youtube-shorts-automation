"""Email approval gate (pure logic, no I/O).

The same rules are implemented in apps_script/approval_watcher.gs. The publisher re-runs
`validate_reply` on the evidence stored in ApprovalLog (and optionally re-fetched from Gmail)
before every upload, so a bug or compromise in one component cannot approve on its own.

Accepted reply = after removing quoted history and an optional signature, exactly one
non-empty line that is exactly `APPROVED VIDEO-YYYYMMDD-NNN` / `REJECTED VIDEO-…` (YES/NO and
APPROVE/REJECT are accepted synonyms). It must arrive before the deadline: the earlier of
request + 24 h and the publish slot minus 15 minutes.
Anything else (extra text, lowercase, two commands, a command only inside quoted text) is
rejected. Missing replies, timeouts and errors never count as approval.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parseaddr

from studio.core.state import State, VideoRecord, parse_iso

COMMAND_RE = re.compile(r"^(YES|NO|APPROVED|REJECTED|APPROVE|REJECT) (VIDEO-\d{8}-\d{3})$")
CMD_MAP = {"YES": "YES", "APPROVED": "YES", "APPROVE": "YES", "NO": "NO", "REJECTED": "NO", "REJECT": "NO"}
SLOT_MARGIN = timedelta(minutes=15)   # approval must arrive this long before the publish slot
_ON_WROTE_START = re.compile(r"^On .*")
_QUOTE_MARKERS = (
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.I),
    re.compile(r"^_{5,}$"),
    re.compile(r"^From: .+"),
    re.compile(r"^Sent from my .+", re.I),
    re.compile(r"^Get Outlook for .+", re.I),
)
CLOCK_SKEW = timedelta(minutes=5)


class ApprovalRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def new_text(body: str) -> list[str]:
    """Lines written in this reply, with quoted history and signature removed."""
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith(">"):
            break
        if line == "--":  # RFC 3676 signature delimiter ("-- " stripped)
            break
        if any(p.match(line) for p in _QUOTE_MARKERS):
            break
        if _ON_WROTE_START.match(line):
            window = " ".join(l.strip() for l in lines[i:i + 3])
            if "wrote:" in window:
                break
        out.append(line)
    return [l for l in out if l]


def parse_command(body: str) -> tuple[str, str]:
    lines = new_text(body)
    if not lines:
        raise ApprovalRejected("empty reply (command may only be inside quoted text)")
    if len(lines) > 1:
        raise ApprovalRejected("ambiguous reply: more than one line of new text")
    m = COMMAND_RE.fullmatch(lines[0])
    if not m:
        raise ApprovalRejected("malformed command; expected exactly 'APPROVED VIDEO-ID' or 'REJECTED VIDEO-ID'")
    return CMD_MAP[m.group(1)], m.group(2)


@dataclass
class ReplyEvidence:
    message_id: str
    thread_id: str
    sender: str             # raw From header
    sent_at: datetime       # Gmail internal date (UTC aware)
    body: str               # plain-text body
    label_ids: list[str] = field(default_factory=list)
    auth_results: str = ""  # Authentication-Results header (external approver only)


def sender_authenticated(ev: ReplyEvidence, approver: str, approver_is_owner: bool) -> bool:
    """Owner mailbox: the reply must carry Gmail's SENT label, i.e. it was really sent from the
    authenticated account (a spoofed message would arrive in INBOX instead).
    External approver: Gmail's own (mx.google.com) Authentication-Results must show an aligned
    DMARC pass for the approver's domain."""
    if approver_is_owner:
        return "SENT" in ev.label_ids
    domain = approver.rsplit("@", 1)[-1].lower()
    ar = " ".join(ev.auth_results.lower().split())
    return (ar.startswith("mx.google.com;") and "dmarc=pass" in ar
            and f"header.from={domain}" in ar and "dkim=pass" in ar)


def validate_reply(rec: VideoRecord, ev: ReplyEvidence, *, approver: str, approver_is_owner: bool,
                   now: datetime, ttl_hours: int, used_message_ids: set[str],
                   expected_state: State = State.AWAITING_APPROVAL) -> str:
    """Return 'YES' or 'NO' for a valid reply, else raise ApprovalRejected."""
    if not approver:
        raise ApprovalRejected("approver email not configured")
    if rec.state != expected_state:
        raise ApprovalRejected(f"record state is {rec.state.value}, expected {expected_state.value}")
    if not rec.approval_thread_id or ev.thread_id != rec.approval_thread_id:
        raise ApprovalRejected("reply is not in the approval request thread")
    if ev.message_id == rec.approval_request_message_id:
        raise ApprovalRejected("message is the approval request itself")
    if ev.message_id in used_message_ids:
        raise ApprovalRejected("message already processed (replay/duplicate)")
    addr = parseaddr(ev.sender)[1].strip().lower()
    if addr != approver.strip().lower():
        raise ApprovalRejected(f"sender {addr or '?'} is not the configured approver")
    if not sender_authenticated(ev, approver, approver_is_owner):
        raise ApprovalRejected("sender authentication failed (not SENT by owner / DKIM+DMARC fail)")
    requested = parse_iso(rec.approval_requested_at)
    if requested is None:
        raise ApprovalRejected("approval request time missing")
    if ev.sent_at.tzinfo is None:
        raise ApprovalRejected("reply timestamp has no timezone")
    if ev.sent_at <= requested:
        raise ApprovalRejected("reply predates the approval request")
    deadline = approval_deadline(rec, ttl_hours)
    if ev.sent_at > deadline:
        raise ApprovalRejected("reply arrived after the approval expired")
    if ev.sent_at > now + CLOCK_SKEW:
        raise ApprovalRejected("reply timestamp is in the future")
    decision, vid = parse_command(ev.body)
    if vid != rec.video_id:
        raise ApprovalRejected(f"command names {vid}, but this thread is for {rec.video_id}")
    return decision


def approval_deadline(rec: VideoRecord, ttl_hours: int) -> datetime:
    """min(request + TTL, publish slot - 15 min): no reply before the evening slot = skipped."""
    deadline = parse_iso(rec.approval_requested_at) + timedelta(hours=ttl_hours)
    slot = parse_iso(rec.proposed_publish_at) if rec.proposed_publish_at else None
    return min(deadline, slot - SLOT_MARGIN) if slot else deadline


def is_expired(rec: VideoRecord, now: datetime, ttl_hours: int) -> bool:
    requested = parse_iso(rec.approval_requested_at)
    return requested is not None and now > approval_deadline(rec, ttl_hours)
