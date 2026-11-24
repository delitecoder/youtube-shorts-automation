"""Wires settings, store and Google services for CLI commands.

DRY_RUN=true  -> state lives in runtime/dry_run_state.json; Drive is read-only (if credentials
                 exist) and every write is recorded in runtime/dry_run_actions.jsonl instead.
DRY_RUN=false -> state lives in the Google Sheets control spreadsheet; Drive writes happen.
"""
from __future__ import annotations

import json
from functools import cached_property

from ..config import RUNTIME_DIR, Settings
from ..state import iso, utcnow
from ..store import JsonFileStore


def load_drive_keys() -> dict:
    """Pull keys/settings from Drive 00_Config/keys.json into the environment (names reported only)."""
    import os
    if not os.environ.get("YTA_PIPELINE_OAUTH") or os.environ.get("YTA_SKIP_DRIVE_KEYS") == "true":
        return {"loaded": [], "note": "skipped (no pipeline credentials)"}
    try:
        from ..integrations.google_auth import build_service
        from ..integrations.keys import load_into_env
        return load_into_env(build_service("drive", "v3", "pipeline"))
    except Exception as e:  # noqa: BLE001 - report, never print values
        return {"loaded": [], "error": f"{type(e).__name__}: {str(e)[:160]}"}


class Ctx:
    def __init__(self, settings: Settings | None = None, profile: str = "pipeline"):
        self.keys_report = {} if settings else load_drive_keys()
        self.s = settings or Settings.from_env()
        self.profile = profile
        RUNTIME_DIR.mkdir(exist_ok=True)

    @cached_property
    def store(self):
        if self.s.dry_run or not self.s.spreadsheet_id:
            if not self.s.dry_run:
                raise RuntimeError("SPREADSHEET_ID required when DRY_RUN=false")
            return JsonFileStore(RUNTIME_DIR / "dry_run_state.json")
        from ..integrations.google_auth import build_service
        from ..integrations.sheets import SheetsStore
        return SheetsStore(build_service("sheets", "v4", self.profile), self.s.spreadsheet_id)

    @cached_property
    def drive(self):
        from ..integrations.drive import Drive
        from ..integrations.google_auth import build_service
        return Drive(build_service("drive", "v3", self.profile), self.s)

    @cached_property
    def youtube(self):
        from ..integrations.google_auth import build_service
        from ..integrations.youtube import YouTube
        return YouTube(build_service("youtube", "v3", self.profile))

    @cached_property
    def yt_analytics(self):
        from ..integrations.google_auth import build_service
        return build_service("youtubeAnalytics", "v2", self.profile)

    def external_write(self, description: str, fn):
        """Run an external side effect (a zero-arg callable), or just record it in DRY_RUN."""
        if self.s.dry_run:
            with open(RUNTIME_DIR / "dry_run_actions.jsonl", "a") as fh:
                fh.write(json.dumps({"at": iso(utcnow()), "action": description}) + "\n")
            return None
        return fn()
