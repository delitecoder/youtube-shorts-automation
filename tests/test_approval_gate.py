from datetime import timedelta

import pytest
from conftest import APPROVER, awaiting_record

from ytauto.approval.gate import (ApprovalRejected, ReplyEvidence, is_expired, new_text,
                                  parse_command, validate_reply)
from ytauto.state import State

VID = "VIDEO-20261124-001"


def ev(t0, body=f"YES {VID}", sender=f"Me <{APPROVER}>", minutes=10, mid="msg-reply",
       thread="thread-1", labels=("SENT",), auth=""):
    return ReplyEvidence(message_id=mid, thread_id=thread, sender=sender,
                         sent_at=t0 + timedelta(minutes=minutes), body=body,
                         label_ids=list(labels), auth_results=auth)


def check(store, t0, e, used=frozenset(), now_minutes=15, **kw):
    rec = awaiting_record(store)
    return validate_reply(rec, e, approver=APPROVER, approver_is_owner=kw.get("owner", True),
                          now=t0 + timedelta(minutes=now_minutes), ttl_hours=24,
                          used_message_ids=set(used))


def test_yes_accepted(store, t0):
    assert check(store, t0, ev(t0)) == "YES"


def test_no_accepted(store, t0):
    assert check(store, t0, ev(t0, body=f"NO {VID}")) == "NO"


def test_yes_with_gmail_quote_and_signature_accepted(store, t0):
    body = (f"YES {VID}\n\n--\nMe\n\nOn Tue, 24 Nov 2026 at 10:30, Bot <{APPROVER}>\nwrote:\n"
            f"> Reply YES {VID} to approve")
    assert check(store, t0, ev(t0, body=body)) == "YES"


@pytest.mark.parametrize("body,reason", [
    (f"yes {VID}", "malformed"),
    (f"YES  {VID}", "malformed"),
    (f"YES {VID} please", "malformed"),
    (f"Sure, YES {VID}", "malformed"),
    (f"YES {VID}\nNO {VID}", "ambiguous"),
    ("", "empty"),
    (f"> YES {VID}", "empty"),                                       # only quoted
    (f"On Mon, Bob wrote:\n> YES {VID}", "empty"),                   # quoted earlier message
    (f"Looks fine\n\nOn Mon, x wrote:\n> YES {VID}", "malformed"),   # approval only in quote
    ("YES VIDEO-20261124-1", "malformed"),
])
def test_malformed_or_quoted_rejected(store, t0, body, reason):
    with pytest.raises(ApprovalRejected, match=reason):
        check(store, t0, ev(t0, body=body))


def test_wrong_sender_rejected(store, t0):
    with pytest.raises(ApprovalRejected, match="not the configured approver"):
        check(store, t0, ev(t0, sender="Mallory <mallory@example.com>"))


def test_spoofed_owner_without_sent_label_rejected(store, t0):
    with pytest.raises(ApprovalRejected, match="authentication"):
        check(store, t0, ev(t0, labels=("INBOX",)))


GOOD_AR = "mx.google.com; dkim=pass header.i=@example.com; spf=pass; dmarc=pass (p=REJECT) header.from=example.com"


def test_external_approver_with_dmarc_pass(store, t0):
    assert check(store, t0, ev(t0, labels=("INBOX",), auth=GOOD_AR), owner=False) == "YES"


@pytest.mark.parametrize("auth", [GOOD_AR.replace("dmarc=pass", "dmarc=fail"),
                                  GOOD_AR.replace("mx.google.com", "evil.example"),
                                  GOOD_AR.replace("header.from=example.com", "header.from=evil.com"), ""])
def test_external_approver_without_dmarc_rejected(store, t0, auth):
    with pytest.raises(ApprovalRejected, match="authentication"):
        check(store, t0, ev(t0, labels=("INBOX",), auth=auth), owner=False)


def test_wrong_video_id_rejected(store, t0):
    with pytest.raises(ApprovalRejected, match="this thread is for"):
        check(store, t0, ev(t0, body="YES VIDEO-20261124-002"))


def test_old_email_before_request_rejected(store, t0):
    with pytest.raises(ApprovalRejected, match="predates"):
        check(store, t0, ev(t0, minutes=-30))


def test_reply_at_exact_request_time_rejected(store, t0):
    with pytest.raises(ApprovalRejected, match="predates"):
        check(store, t0, ev(t0, minutes=0))


def test_expired_approval_rejected(store, t0):
    with pytest.raises(ApprovalRejected, match="expired"):
        check(store, t0, ev(t0, minutes=24 * 60 + 1), now_minutes=24 * 60 + 2)


def test_future_timestamp_rejected(store, t0):
    with pytest.raises(ApprovalRejected, match="future"):
        check(store, t0, ev(t0, minutes=60), now_minutes=15)


def test_duplicate_replayed_message_rejected(store, t0):
    with pytest.raises(ApprovalRejected, match="replay"):
        check(store, t0, ev(t0), used={"msg-reply"})


def test_request_message_itself_rejected(store, t0):
    with pytest.raises(ApprovalRejected, match="request itself"):
        check(store, t0, ev(t0, mid="msg-request"))


def test_other_thread_rejected(store, t0):
    with pytest.raises(ApprovalRejected, match="thread"):
        check(store, t0, ev(t0, thread="thread-other"))


def test_wrong_state_rejected(store, t0):
    rec = awaiting_record(store)
    rec = store.transition(rec.video_id, State.AWAITING_APPROVAL, State.REJECTED, rec.version)
    with pytest.raises(ApprovalRejected, match="state"):
        validate_reply(rec, ev(t0), approver=APPROVER, approver_is_owner=True,
                       now=t0 + timedelta(minutes=15), ttl_hours=24, used_message_ids=set())


def test_missing_approver_config_rejects(store, t0):
    rec = awaiting_record(store)
    with pytest.raises(ApprovalRejected, match="not configured"):
        validate_reply(rec, ev(t0), approver="", approver_is_owner=True,
                       now=t0 + timedelta(minutes=15), ttl_hours=24, used_message_ids=set())


def test_expiry_helper(store, t0):
    rec = awaiting_record(store)
    # publish slot = request + 9 h -> approvals close at request + 8 h 45 min
    assert not is_expired(rec, t0 + timedelta(hours=8, minutes=44), 24)
    assert is_expired(rec, t0 + timedelta(hours=8, minutes=46), 24)


def test_new_text_strips_mobile_signature():
    assert new_text(f"YES {VID}\nSent from my iPhone") == [f"YES {VID}"]
    assert parse_command(f"  YES {VID}  \r\n") == ("YES", VID)


@pytest.mark.parametrize("word,decision", [("APPROVED", "YES"), ("APPROVE", "YES"), ("REJECTED", "NO"), ("REJECT", "NO")])
def test_word_synonyms(store, t0, word, decision):
    assert check(store, t0, ev(t0, body=f"{word} {VID}")) == decision


def test_reply_after_slot_deadline_is_skipped(store, t0):
    # conftest: publish slot is request + 9 h -> deadline is request + 8 h 45 min
    with pytest.raises(ApprovalRejected, match="expired"):
        check(store, t0, ev(t0, minutes=8 * 60 + 50), now_minutes=8 * 60 + 51)


def test_reply_just_before_slot_deadline_is_accepted(store, t0):
    assert check(store, t0, ev(t0, minutes=8 * 60 + 40), now_minutes=8 * 60 + 41) == "YES"
