#!/usr/bin/env python3
"""Push apps_script/approval_watcher.gs + appsscript.json to the owner's Apps Script project.

Needs YTA_SCRIPT_OAUTH (script_admin profile, created with
`python scripts/authorize.py --profile script_admin ...`). Only the owner can grant it.
Finds the project by name in Drive unless --script-id is given. After a push the owner must run
`setup` once in the editor if new permissions (e.g. YouTube) were added.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from googleapiclient.discovery import build  # noqa: E402

from ytauto.integrations.google_auth import credentials  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--script-id")
a = ap.parse_args()
sid = a.script_id
if not sid:
    drive = build("drive", "v3", credentials=credentials("pipeline"), cache_discovery=False)
    hits = drive.files().list(q="mimeType='application/vnd.google-apps.script' and trashed=false and "
                                "name='YouTube Shorts Approval Watcher'", fields="files(id)").execute()["files"]
    if len(hits) != 1:
        sys.exit(f"expected exactly one 'YouTube Shorts Approval Watcher' project, found {len(hits)}")
    sid = hits[0]["id"]
svc = build("script", "v1", credentials=credentials("script_admin"), cache_discovery=False)
files = [{"name": "Code", "type": "SERVER_JS", "source": (ROOT / "apps_script/approval_watcher.gs").read_text()},
         {"name": "appsscript", "type": "JSON", "source": (ROOT / "apps_script/appsscript.json").read_text()}]
svc.projects().updateContent(scriptId=sid, body={"files": files}).execute()
print("Apps Script updated. If this added a permission, open the project and run 'setup' once.")
