"""Cloud Run publisher service.

POST /v1/approved   body {"video_id": "VIDEO-YYYYMMDD-NNN"}  (HMAC-signed by Apps Script)
GET  /healthz

The request only *nominates* a video. The Publisher independently re-validates the approval
ledger, the Gmail message, the checksum and the production flags before any upload.
No model (Claude) calls happen here.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys

from flask import Flask, jsonify, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, "/app/src")

from ytauto.approval.webhook_auth import NonceCache, WebhookAuthError, verify  # noqa: E402

VIDEO_ID_RE = re.compile(r"^VIDEO-\d{8}-\d{3}$")
app = Flask(__name__)
nonces = NonceCache()
log = logging.getLogger("publisher")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_publisher = None


def get_publisher():
    """Built lazily so the health check works without credentials."""
    global _publisher
    if _publisher is None:
        from ytauto.config import Settings
        from ytauto.integrations.drive import Drive
        from ytauto.integrations.gmail_verify import GmailVerifier
        from ytauto.integrations.google_auth import build_service
        from ytauto.integrations.sheets import SheetsStore
        from ytauto.integrations.youtube import YouTube
        from ytauto.publishing.publisher import Publisher

        s = Settings.from_env()
        store = SheetsStore(build_service("sheets", "v4", "publisher"), s.spreadsheet_id)
        _publisher = Publisher(
            s, store, Drive(build_service("drive", "v3", "publisher"), s),
            YouTube(build_service("youtube", "v3", "publisher")),
            GmailVerifier(build_service("gmail", "v1", "publisher")) if s.publisher_verify_gmail else None,
            workdir="/tmp")
    return _publisher


@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


@app.post("/v1/approved")
def approved():
    body = request.get_data(cache=False, as_text=False)
    try:
        verify(os.environ.get("WEBHOOK_SECRET", ""), dict(request.headers), body, nonces)
    except WebhookAuthError as e:
        log.warning("webhook auth rejected: %s", e)          # no secrets/body logged
        return jsonify(ok=False, error="unauthorized"), 401
    try:
        vid = json.loads(body)["video_id"]
    except (ValueError, KeyError, TypeError):
        return jsonify(ok=False, error="bad request"), 400
    if not isinstance(vid, str) or not VIDEO_ID_RE.match(vid):
        return jsonify(ok=False, error="bad video id"), 400
    result = get_publisher().publish(vid)
    log.info("publish %s -> %s", vid, result.status)
    code = 200 if result.status in ("scheduled", "uploaded_private", "dry_run", "disabled", "duplicate") else 409
    return jsonify(ok=code == 200, **result.to_dict()), code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
