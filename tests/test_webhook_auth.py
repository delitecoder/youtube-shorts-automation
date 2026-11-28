import pytest

from studio.youtube.approval.webhook_auth import NonceCache, WebhookAuthError, sign, verify

SECRET = "s" * 40
BODY = b'{"video_id": "VIDEO-20261124-001"}'


def headers(ts="1000", nonce="n" * 32, body=BODY, secret=SECRET):
    return {"X-YTA-Timestamp": ts, "X-YTA-Nonce": nonce, "X-YTA-Signature": sign(secret, ts, nonce, body)}


def test_valid_signature():
    verify(SECRET, headers(), BODY, NonceCache(), now=1010)


def test_bad_signature():
    with pytest.raises(WebhookAuthError, match="signature"):
        verify(SECRET, headers(secret="x" * 40), BODY, NonceCache(), now=1010)


def test_tampered_body():
    with pytest.raises(WebhookAuthError, match="signature"):
        verify(SECRET, headers(), b'{"video_id": "VIDEO-20261124-002"}', NonceCache(), now=1010)


def test_stale_timestamp():
    with pytest.raises(WebhookAuthError, match="window"):
        verify(SECRET, headers(), BODY, NonceCache(), now=1000 + 301)


def test_replayed_nonce():
    cache = NonceCache()
    verify(SECRET, headers(), BODY, cache, now=1010)
    with pytest.raises(WebhookAuthError, match="replay"):
        verify(SECRET, headers(), BODY, cache, now=1011)


def test_short_secret_refused():
    with pytest.raises(WebhookAuthError, match="secret"):
        verify("short", headers(secret="short"), BODY, NonceCache(), now=1010)


def test_missing_headers():
    with pytest.raises(WebhookAuthError, match="missing"):
        verify(SECRET, {}, BODY, NonceCache(), now=1010)
