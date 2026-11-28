"""Google Sheets implementation of the workflow Store (spreadsheet YouTube_Automation_Control)."""
from __future__ import annotations

import time

from studio.core.state import COLUMNS, ConflictError, State, VideoRecord, iso, utcnow
from studio.storage.store import TABS, _apply

SPREADSHEET_NAME = "YouTube_Automation_Control"
# Retries on dropped connections (e.g. the keep-alive socket closed by Google during a long render:
# BrokenPipeError on the next call). googleapiclient reconnects and retries reads/writes.
RETRIES = 3


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class SheetsStore:
    def __init__(self, service, spreadsheet_id: str):
        self.svc = service.spreadsheets()
        self.sid = spreadsheet_id

    # ---------------- schema
    @classmethod
    def create_spreadsheet(cls, service) -> str:
        body = {"properties": {"title": SPREADSHEET_NAME},
                "sheets": [{"properties": {"title": t}} for t in TABS]}
        sid = service.spreadsheets().create(body=body, fields="spreadsheetId").execute(num_retries=RETRIES)["spreadsheetId"]
        store = cls(service, sid)
        store.ensure_schema()
        return sid

    def ensure_schema(self) -> None:
        meta = self.svc.get(spreadsheetId=self.sid, fields="sheets(properties(title))").execute(num_retries=RETRIES)
        have = {s["properties"]["title"] for s in meta["sheets"]}
        missing = [t for t in TABS if t not in have]
        if missing:
            self.svc.batchUpdate(spreadsheetId=self.sid, body={"requests": [
                {"addSheet": {"properties": {"title": t}}} for t in missing]}).execute(num_retries=RETRIES)
        meta = self.svc.get(spreadsheetId=self.sid, fields="sheets(properties(sheetId,title))").execute(num_retries=RETRIES)
        self.svc.batchUpdate(spreadsheetId=self.sid, body={"requests": [
            {"repeatCell": {"range": {"sheetId": sh["properties"]["sheetId"]},
                            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}}},
                            "fields": "userEnteredFormat.numberFormat"}}
            for sh in meta["sheets"] if sh["properties"]["title"] in TABS]}).execute(num_retries=RETRIES)
        for tab, cols in TABS.items():
            hdr = self._values(f"{tab}!1:1")
            if not hdr:
                self._write(f"{tab}!A1", [list(cols)])
            elif tuple(hdr[0][:len(cols)]) != cols:
                raise RuntimeError(f"Sheet tab {tab} has unexpected headers; fix manually")

    # ---------------- low level
    def _values(self, rng: str) -> list[list[str]]:
        return self.svc.values().get(spreadsheetId=self.sid, range=rng,
                                     valueRenderOption="FORMATTED_VALUE").execute(num_retries=RETRIES).get("values", [])

    def _write(self, rng: str, values: list[list[str]]) -> None:
        self.svc.values().update(spreadsheetId=self.sid, range=rng, valueInputOption="RAW",
                                 body={"values": values}).execute(num_retries=RETRIES)

    def _table(self, tab: str) -> list[dict]:
        vals = self._values(f"{tab}!A1:{_col(len(TABS[tab]))}")
        if not vals:
            return []
        hdr = vals[0]
        return [{h: str(r[i]) if i < len(r) else "" for i, h in enumerate(hdr)} for r in vals[1:]]

    def _find(self, video_id: str) -> tuple[int, dict] | tuple[None, None]:
        for i, r in enumerate(self._table("VideoQueue")):
            if r.get("video_id") == video_id:
                return i + 2, r  # sheet row number (1-based, header row 1)
        return None, None

    # ---------------- Store API
    def get(self, video_id):
        _, r = self._find(video_id)
        return VideoRecord.from_row(r) if r else None

    def all(self):
        return [VideoRecord.from_row(r) for r in self._table("VideoQueue") if r.get("video_id")]

    def create(self, rec):
        if self._find(rec.video_id)[0]:
            raise ConflictError(f"{rec.video_id} already exists")
        now = iso(utcnow())
        rec.created_at, rec.updated_at = rec.created_at or now, now
        import secrets
        rec.write_token = secrets.token_hex(8)
        row = rec.to_row()
        self.svc.values().append(spreadsheetId=self.sid, range="VideoQueue!A1",
                                 valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                                 body={"values": [[row[c] for c in COLUMNS]]}).execute(num_retries=RETRIES)
        dupes = [r for r in self._table("VideoQueue") if r.get("video_id") == rec.video_id]
        if len(dupes) != 1 or dupes[0].get("write_token") != rec.write_token:
            raise ConflictError(f"{rec.video_id} was created concurrently; manual check required")
        return rec

    def _mutate(self, video_id, expected, new, expected_version, updates):
        n, r = self._find(video_id)
        if not n:
            raise KeyError(video_id)
        out = _apply(VideoRecord.from_row(r), expected, new, expected_version, updates)
        row = out.to_row()
        self._write(f"VideoQueue!A{n}:{_col(len(COLUMNS))}{n}", [[row[c] for c in COLUMNS]])
        time.sleep(0.5)
        check = self.get(video_id)
        if not check or check.write_token != out.write_token:
            raise ConflictError(f"{video_id}: lost a concurrent write; manual check required")
        return out

    def transition(self, video_id, expected, new, expected_version, **updates):
        return self._mutate(video_id, expected, new, expected_version, updates)

    def update_fields(self, video_id, expected_version, **updates):
        return self._mutate(video_id, None, None, expected_version, updates)

    def append(self, tab, row):
        if tab not in TABS or tab == "VideoQueue":
            raise ValueError(tab)
        self.svc.values().append(spreadsheetId=self.sid, range=f"{tab}!A1", valueInputOption="RAW",
                                 insertDataOption="INSERT_ROWS",
                                 body={"values": [[str(row.get(c, "")) for c in TABS[tab]]]}).execute(num_retries=RETRIES)

    def rows(self, tab):
        return self._table(tab)

    def get_config(self, key, default=""):
        val = default
        for r in self._table("Configuration"):
            if r.get("key") == key:
                val = r.get("value", "")
        return val

    def set_config(self, key, value):
        self.append("Configuration", {"key": key, "value": value, "updated_at": iso(utcnow())})
