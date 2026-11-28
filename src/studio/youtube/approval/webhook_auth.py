"""HMAC-SHA256 signing for Apps Script -> publisher calls.

Headers:  X-YTA-Timestamp: unix seconds
          X-YTA-Nonce:     random hex, single use
          X-YTA-Signature: hex(HMAC_SHA256(secret, f"{timestamp}.{nonce}.{body}"))
Requests older/newer than 300 s or with a reused nonce are rejected. Even a replay that slips
past the in-memory nonce cache (e.g. after an instance restart) is harmless because the
publisher is idempotent on the workflow state.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time

MAX_SKEW_S = 300


class WebhookAuthError(Exception):
    pass


def sign(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    msg = timestamp.encode() + b"." + nonce.encode() + b"." + body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


class NonceCache:
    def __init__(self, ttl: int = 2 * MAX_SKEW_S):
        self.ttl, self._seen, self._lock = ttl, {}, threading.Lock()

    def check_and_add(self, nonce: str, now: float) -> bool:
        with self._lock:
            for k in [k for k, t in self._seen.items() if now - t > self.ttl]:
                del self._seen[k]
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True


def verify(secret: str, headers: dict, body: bytes, nonces: NonceCache, now: float | None = None) -> None:
    if not secret or len(secret) < 32:
        raise WebhookAuthError("webhook secret missing or too short")
    h = {k.lower(): v for k, v in headers.items()}
    ts, nonce, sig = h.get("x-yta-timestamp", ""), h.get("x-yta-nonce", ""), h.get("x-yta-signature", "")
    if not (ts.isdigit() and 16 <= len(nonce) <= 128 and sig):
        raise WebhookAuthError("missing or malformed auth headers")
    now = time.time() if now is None else now
    if abs(now - int(ts)) > MAX_SKEW_S:
        raise WebhookAuthError("timestamp outside allowed window")
    if not hmac.compare_digest(sign(secret, ts, nonce, body), sig.lower()):
        raise WebhookAuthError("bad signature")
    if not nonces.check_and_add(nonce, now):
        raise WebhookAuthError("nonce reused (replay)")
