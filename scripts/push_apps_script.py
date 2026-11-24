#!/usr/bin/env python3
"""Push apps_script/approval_watcher.gs + appsscript.json to the owner's Apps Script project.

OWNER TOOL: run it yourself. Claude sessions are blocked from running it (changing the code that
enforces the approval gate is treated as undermining oversight), which is intended.

    python scripts/push_apps_script.py --if-changed      # used by the daily cloud run

Needs YTA_SCRIPT_OAUTH (script_admin profile, granted once by the owner with
`python scripts/authorize.py --profile script_admin ...`). Without it this exits quietly (code 0).
--if-changed only pushes when the repo version differs from the last pushed version (hash kept in
the Configuration tab). When the manifest (permissions) changes, the owner is emailed to run
`setup` once, because Google requires the owner to approve new permissions personally.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script-id")
    ap.add_argument("--if-changed", action="store_true")
    a = ap.parse_args()
    pipe = json.loads(os.environ.get("YTA_PIPELINE_OAUTH", "{}") or "{}")
    if not os.environ.get("YTA_SCRIPT_OAUTH") and \
            "https://www.googleapis.com/auth/script.projects" not in pipe.get("scopes", []):
        print(json.dumps({"pushed": False, "reason": "YTA_SCRIPT_OAUTH not set (owner has not granted script updates)"}))
        return 0
    from googleapiclient.discovery import build

    from ytauto.integrations.google_auth import credentials
    from ytauto.orchestration.context import Ctx
    from ytauto.store import log_error

    code = (ROOT / "apps_script/approval_watcher.gs").read_text()
    manifest = (ROOT / "apps_script/appsscript.json").read_text()
    h_code = hashlib.sha256(code.encode()).hexdigest()[:16]
    h_man = hashlib.sha256(manifest.encode()).hexdigest()[:16]
    ctx = Ctx()
    last = json.loads(ctx.store.get_config("apps_script_pushed", "{}") or "{}")
    if a.if_changed and last.get("code") == h_code and last.get("manifest") == h_man:
        print(json.dumps({"pushed": False, "reason": "already up to date", "version": h_code}))
        return 0
    sid = a.script_id
    if not sid:
        drive = build("drive", "v3", credentials=credentials("pipeline"), cache_discovery=False)
        hits = drive.files().list(q="mimeType='application/vnd.google-apps.script' and trashed=false and "
                                    "name='YouTube Shorts Approval Watcher'", fields="files(id)").execute()["files"]
        if len(hits) != 1:
            print(json.dumps({"pushed": False, "reason": f"found {len(hits)} matching Apps Script projects"}))
            return 2
        sid = hits[0]["id"]
    svc = build("script", "v1", credentials=credentials("script_admin"), cache_discovery=False)
    files = [{"name": "Code", "type": "SERVER_JS", "source": code},
             {"name": "appsscript", "type": "JSON", "source": manifest}]
    svc.projects().updateContent(scriptId=sid, body={"files": files}).execute()
    ctx.store.set_config("apps_script_pushed", json.dumps({"code": h_code, "manifest": h_man}))
    needs_setup = last.get("manifest") != h_man
    if needs_setup and not ctx.s.dry_run:
        log_error(ctx.store, "apps_script_update",
                  "The approval Apps Script was updated automatically and needs a new permission. Open "
                  f"https://script.google.com/d/{sid}/edit, choose 'setup', click Run, then Allow (once).", "", "INFO")
    print(json.dumps({"pushed": True, "version": h_code, "owner_must_run_setup": needs_setup}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
