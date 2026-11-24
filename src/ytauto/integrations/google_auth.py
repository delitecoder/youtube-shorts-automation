"""OAuth credentials for the two least-privilege profiles.

pipeline  – used by the Claude Code daily task. It can read/write the automation Drive folders
            and the control spreadsheet and read analytics, but it CANNOT upload to YouTube.
publisher – used only by the Cloud Run publisher. It can upload to YouTube, read Drive files
            and the spreadsheet, and read the single approval reply in Gmail.

Each profile's authorized-user JSON (client_id, client_secret, refresh_token) is supplied in an
environment variable that is populated from a protected store (Claude environment secret / GCP
Secret Manager). It is never written to the repository or printed.
"""
from __future__ import annotations

import json
import os

PROFILES: dict[str, dict] = {
    "pipeline": {
        "env": "YTA_PIPELINE_OAUTH",
        "scopes": [
            "https://www.googleapis.com/auth/drive",            # folder allowlist enforced in code
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ],
    },
    # OPTIONAL and owner-granted only: lets Claude push approval_watcher.gs updates. Anyone holding
    # this token can change the upload/approval code, so it weakens the approval boundary.
    "script_admin": {
        "env": "YTA_SCRIPT_OAUTH",
        "scopes": ["https://www.googleapis.com/auth/script.projects"],
    },
    "publisher": {
        "env": "YTA_PUBLISHER_OAUTH",
        "scopes": [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/gmail.readonly",    # re-verify the approval reply
        ],
    },
}


class MissingCredentials(RuntimeError):
    pass


def credentials(profile: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    spec = PROFILES[profile]
    raw = os.environ.get(spec["env"], "")
    if not raw:
        raise MissingCredentials(f"{spec['env']} is not set (see docs/deployment.md)")
    info = json.loads(raw)
    creds = Credentials.from_authorized_user_info(info, spec["scopes"])
    if not creds.valid:
        creds.refresh(Request())
    return creds


def build_service(name: str, version: str, profile: str):
    from googleapiclient.discovery import build

    return build(name, version, credentials=credentials(profile), cache_discovery=False)
