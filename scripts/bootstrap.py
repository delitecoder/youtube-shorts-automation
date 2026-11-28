#!/usr/bin/env python3
"""One-command setup on YOUR computer (needs a browser for Google's "Allow" screen).

    pip install -r requirements-setup.txt
    python scripts/bootstrap.py --client-secret client_secret.json --approver you@gmail.com

What it does (safe to run again; it reuses what already exists):
  1. Google login for the daily pipeline (browser: choose your account, click Allow)
     -> saves pipeline_token.json next to this repo (0600, git-ignored). Least privilege: it
     cannot upload to YouTube and cannot edit Apps Script.
  2. Creates YouTubeAutomation/ folders in Drive, 00_Config/keys.json and the spreadsheet
     YouTube_Automation_Control, then fills keys.json with the IDs + approver (DRY_RUN=true).
  3. Second, separate login (Allow again) used ONLY to create the Apps Script approval watcher
     project and upload its code; that token is not saved.
  4. Prints the 3 remaining clicks.
Nothing is uploaded to YouTube and no email is sent by this script.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

APIS = ["youtube.googleapis.com", "youtubeanalytics.googleapis.com", "drive.googleapis.com",
        "sheets.googleapis.com", "gmail.googleapis.com", "texttospeech.googleapis.com",
        "script.googleapis.com"]
SCRIPT_SCOPES = ["https://www.googleapis.com/auth/script.projects"]


def login(client_secret: str, scopes: list[str]):
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, scopes)
    return flow.run_local_server(port=0, prompt="consent", access_type="offline",
                                 authorization_prompt_message="Opening your browser for Google sign-in… {url}")


def save_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def merge_keys(existing: dict, ids: dict, spreadsheet_id: str, approver: str, script_id: str = "") -> dict:
    """IDs are always refreshed; values the owner already typed are kept."""
    out = dict(existing)
    out["SPREADSHEET_ID"] = spreadsheet_id
    out["DRIVE_FOLDER_IDS"] = ids
    out["ARCHIVE_FOLDER_ID"] = ids["archive"]
    if approver:
        out["APPROVER_EMAIL"] = approver.strip().lower()
    out.setdefault("DRY_RUN", "true")
    if not str(out.get("DRY_RUN", "")).strip():
        out["DRY_RUN"] = "true"
    if script_id:
        out["_apps_script_id"] = script_id
    return out


def script_files() -> list[dict]:
    return [{"name": "Code", "type": "SERVER_JS",
             "source": (ROOT / "apps_script" / "approval_watcher.gs").read_text()},
            {"name": "appsscript", "type": "JSON",
             "source": (ROOT / "apps_script" / "appsscript.json").read_text()}]


def push_script_only(client_secret: str, script_id: str) -> int:
    from googleapiclient.discovery import build
    print("Updating the Apps Script code (browser opens; click Allow)…")
    svc = build("script", "v1", credentials=login(client_secret, SCRIPT_SCOPES), cache_discovery=False)
    svc.projects().updateContent(scriptId=script_id, body={"files": script_files()}).execute()
    print(f"Done. Open https://script.google.com/d/{script_id}/edit and run 'setup' once more.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-secret", required=True, help="OAuth Desktop client JSON from Google Cloud")
    ap.add_argument("--approver", required=True, help="Gmail address that approves videos")
    ap.add_argument("--skip-apps-script", action="store_true")
    ap.add_argument("--apps-script-only", metavar="SCRIPT_ID",
                    help="only push the latest approval_watcher.gs to this existing script (one Allow)")
    a = ap.parse_args()
    if a.apps_script_only:
        return push_script_only(a.client_secret, a.apps_script_only)

    cs = json.loads(Path(a.client_secret).read_text())
    project = (cs.get("installed") or cs.get("web") or {}).get("project_id", "")
    if "installed" not in cs:
        sys.exit("This must be a *Desktop app* OAuth client (JSON with an 'installed' section).")
    print("\nStep 0 — make sure these APIs are enabled (one click, then come back):")
    print(f"  https://console.cloud.google.com/flows/enableapi?apiid={','.join(APIS)}&project={project}\n")

    from googleapiclient.discovery import build
    from studio.core.config import Settings
    from studio.storage.drive import Drive
    from studio.core.google_auth import PROFILES
    from studio.storage.sheets import SheetsStore

    # 1) pipeline login
    print("Step 1 — Google login for the daily pipeline (browser opens; click Allow)…")
    creds = login(a.client_secret, PROFILES["pipeline"]["scopes"])
    token_path = ROOT / "pipeline_token.json"
    save_private(token_path, creds.to_json())
    drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets_svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # 2) Drive structure, keys.json, spreadsheet
    print("Step 2 — creating Drive folders, keys.json and the control spreadsheet…")
    ids = Drive(drive_svc, Settings()).ensure_structure()
    q = f"name = 'keys.json' and trashed = false and '{ids['config']}' in parents"
    keys_id = drive_svc.files().list(q=q, fields="files(id)").execute()["files"][0]["id"]
    existing = json.loads(drive_svc.files().get_media(fileId=keys_id).execute() or b"{}")
    sid = existing.get("SPREADSHEET_ID") or ""
    if sid:
        SheetsStore(sheets_svc, sid).ensure_schema()
    else:
        sid = SheetsStore.create_spreadsheet(sheets_svc)
        drive_svc.files().update(fileId=sid, addParents=ids["config"], removeParents="root",
                                 fields="id").execute()

    # 3) Apps Script project
    script_id = existing.get("_apps_script_id", "")
    if not a.skip_apps_script:
        print("Step 3 — second login, only to create the Apps Script approval watcher (click Allow)…")
        screds = login(a.client_secret, SCRIPT_SCOPES)          # not saved anywhere
        svc = build("script", "v1", credentials=screds, cache_discovery=False)
        try:
            if not script_id:
                script_id = svc.projects().create(body={"title": "YouTube Shorts Approval Watcher"}).execute()["scriptId"]
            svc.projects().updateContent(scriptId=script_id, body={"files": script_files()}).execute()
        except Exception as e:  # noqa: BLE001
            print(f"  Could not create the script automatically ({str(e)[:160]}).")
            print("  Turn on 'Google Apps Script API' at https://script.google.com/home/usersettings and rerun,")
            print("  or paste apps_script/approval_watcher.gs into a new project at script.google.com.")
            script_id = script_id or ""

    from googleapiclient.http import MediaInMemoryUpload
    merged = merge_keys(existing, ids, sid, a.approver, script_id)
    drive_svc.files().update(fileId=keys_id, media_body=MediaInMemoryUpload(
        json.dumps(merged, indent=2).encode(), mimetype="application/json")).execute()
    perms = drive_svc.files().get(fileId=keys_id, fields="permissions(type)").execute().get("permissions", [])
    if any(p["type"] in ("anyone", "domain") for p in perms):
        print("  WARNING: keys.json is shared publicly — set its sharing to Restricted.")

    print("\nDone. Remaining clicks for you:")
    n = 1
    if script_id:
        print(f"  {n}. Open https://script.google.com/d/{script_id}/edit → choose function 'setup' → Run → Allow.")
        n += 1
    print(f"  {n}. claude.ai/code → environment 'Youtube Automation' → Edit → Environment variables:")
    print(f"     name YTA_PIPELINE_OAUTH, value = the whole content of {token_path.name} (open it in a text editor).")
    print(f"     Then delete {token_path.name} from this computer.")
    n += 1
    print(f"  {n}. Tell Claude 'setup done' — it will run a test.")
    print(f"\n(Spreadsheet: https://docs.google.com/spreadsheets/d/{sid}  ·  keys.json is in YouTubeAutomation/00_Config)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
