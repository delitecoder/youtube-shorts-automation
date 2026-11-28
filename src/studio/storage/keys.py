"""Load keys/settings from a private JSON file in Google Drive: YouTubeAutomation/00_Config/keys.json.

Bootstrap: reading Drive needs one credential first, so YTA_PIPELINE_OAUTH stays in the Claude
environment. Everything else can live in keys.json. Locating the file: YTA_KEYS_FILE_ID (an ID,
not a secret) if set, otherwise by name YouTubeAutomation/00_Config/keys.json in My Drive.

Safety rules (the file is refused, never partly used, if any fails):
  * it must be owned by the account itself and NOT shared with "anyone" or a whole domain;
  * only whitelisted names are read; unknown names are ignored and reported by name only;
  * values already set in the real environment win (so the owner can override);
  * values are never printed or logged — only key names are reported.
"""
from __future__ import annotations

import json
import os

ALLOWED = {
    # secrets
    "GOOGLE_TTS_API_KEY", "GOOGLE_VISION_API_KEY", "BLUESKY_APP_PASSWORD",
    # settings / ids (not secret, but private)
    "APPROVER_EMAIL", "SPREADSHEET_ID", "DRIVE_FOLDER_IDS", "IMAGE_API_CONTACT", "BLUESKY_HANDLE",
    "DRY_RUN", "PROMOTION_ENABLED", "PUBLISH_TIME", "LONG_PUBLISH_TIME", "PUBLISH_TIME_TA", "LONG_PUBLISH_TIME_TA",
    "VIDEO_LANGUAGES", "TTS_VOICE_TA", "SYNTHETIC_MEDIA", "PUBLISH_TIMEZONE", "TTS_PROVIDER", "TTS_VOICE",
    "TTS_FALLBACKS", "TTS_MONTHLY_CHAR_BUDGET", "KOKORO_VOICE", "IMAGE_LICENSE_MODE",
    "VISION_PROVIDERS", "VLM_MODEL", "GOOGLE_VISION_ENABLED", "VISION_MONTHLY_UNIT_BUDGET",
    "MUSIC_ENABLED", "SFX_ENABLED", "OUTRO_ENABLED", "OUTRO_TEXT", "YOUTUBE_CATEGORY_ID",
    "PUBLISHER_URL", "ARCHIVE_FOLDER_ID",      # read by Apps Script; harmless for the pipeline
}
NEVER_FROM_DRIVE = {"UPLOAD_ENABLED", "PUBLICATION_ENABLED", "YTA_PUBLISHER_OAUTH", "WEBHOOK_SECRET"}

TEMPLATE = {
    "_readme": "Private file. Do not share it. Fill the values you use; leave the rest empty. "
               "Upload/publish flags and the publisher credentials are NOT read from here.",
    "APPROVER_EMAIL": "", "SPREADSHEET_ID": "", "DRIVE_FOLDER_IDS": {},
    "GOOGLE_TTS_API_KEY": "", "GOOGLE_VISION_API_KEY": "", "GOOGLE_VISION_ENABLED": "false",
    "BLUESKY_HANDLE": "", "BLUESKY_APP_PASSWORD": "", "IMAGE_API_CONTACT": "",
    "DRY_RUN": "true", "PROMOTION_ENABLED": "false", "IMAGE_LICENSE_MODE": "owner_approval",
    "PUBLISHER_URL": "", "ARCHIVE_FOLDER_ID": "",
}


class KeysFileRejected(RuntimeError):
    pass


def _find_by_name(svc) -> str | None:
    parent = "root"
    for name in ("YouTubeAutomation", "00_Config"):
        q = (f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false "
             f"and '{parent}' in parents")
        hits = svc.files().list(q=q, fields="files(id)", pageSize=2).execute().get("files", [])
        if len(hits) != 1:
            return None
        parent = hits[0]["id"]
    q = f"name = 'keys.json' and trashed = false and '{parent}' in parents"
    hits = svc.files().list(q=q, fields="files(id)", pageSize=2).execute().get("files", [])
    return hits[0]["id"] if len(hits) == 1 else None


def check_private(meta: dict) -> None:
    if not meta.get("ownedByMe"):
        raise KeysFileRejected("keys.json is not owned by this account")
    for p in meta.get("permissions", []) or []:
        if p.get("type") in ("anyone", "domain"):
            raise KeysFileRejected("keys.json is shared publicly or with a domain — unshare it first")


def parse(raw: bytes | str) -> tuple[dict[str, str], list[str]]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise KeysFileRejected("keys.json must be a JSON object")
    values, ignored = {}, []
    for k, v in data.items():
        if k.startswith("_"):
            continue
        if k not in ALLOWED:
            ignored.append(k)
            continue
        if v in ("", None, {}, []):
            continue
        values[k] = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
    return values, ignored


def load_into_env(svc, env=os.environ, exclude: set[str] = frozenset()) -> dict:
    """Returns a report with key NAMES only. `exclude` names are never taken from the file."""
    file_id = env.get("YTA_KEYS_FILE_ID") or _find_by_name(svc)
    if not file_id:
        return {"loaded": [], "note": "no keys.json found (YouTubeAutomation/00_Config/keys.json)"}
    meta = svc.files().get(fileId=file_id, fields="id,ownedByMe,permissions(type,role)").execute()
    check_private(meta)
    raw = svc.files().get_media(fileId=file_id).execute()
    values, ignored = parse(raw)
    loaded, kept = [], []
    for k, v in values.items():
        if k in exclude:
            continue
        if env.get(k):
            kept.append(k)
        else:
            env[k] = v
            loaded.append(k)
    return {"loaded": sorted(loaded), "env_overrides": sorted(kept), "ignored": sorted(ignored)}
