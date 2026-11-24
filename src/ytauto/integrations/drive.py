"""Google Drive access restricted to the configured automation folders.

Every read/move/download first checks that the file's parent is one of the configured folder
IDs, so the rest of the Drive is never listed or downloaded. Downloads stream to disk while
computing SHA-256; source bytes are never modified.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from ..config import FOLDER_KEYS, FOLDER_NAMES, Settings

FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = ("id,name,mimeType,size,md5Checksum,sha256Checksum,parents,ownedByMe,"
               "description,createdTime,modifiedTime,trashed,videoMediaMetadata,webViewLink")


class OutsideAllowedFolders(PermissionError):
    pass


def sha256_file(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while b := fh.read(chunk):
            h.update(b)
    return h.hexdigest()


class Drive:
    def __init__(self, service, settings: Settings):
        self.svc = service
        self.settings = settings
        self.allowed = {v for v in settings.folders.values() if v}

    # ---- guards
    def _check_parent(self, meta: dict) -> None:
        if meta.get("trashed") or not set(meta.get("parents", [])) & self.allowed:
            raise OutsideAllowedFolders(f"file {meta.get('id')} is not in an automation folder")

    def metadata(self, file_id: str) -> dict:
        meta = self.svc.files().get(fileId=file_id, fields=FILE_FIELDS,
                                    supportsAllDrives=True).execute()
        self._check_parent(meta)
        return meta

    # ---- reads
    def list_folder(self, folder_key: str, mime_prefix: str | None = None, limit: int = 50) -> list[dict]:
        fid = self.settings.folder(folder_key)
        q = f"'{fid}' in parents and trashed = false and mimeType != '{FOLDER_MIME}'"
        if mime_prefix:
            q += f" and mimeType contains '{mime_prefix}'"
        res = self.svc.files().list(q=q, fields=f"files({FILE_FIELDS})", orderBy="createdTime",
                                    pageSize=min(limit, 100), supportsAllDrives=True,
                                    includeItemsFromAllDrives=True).execute()
        return res.get("files", [])

    def download(self, file_id: str, dest: Path) -> tuple[Path, str]:
        from googleapiclient.http import MediaIoBaseDownload

        self.metadata(file_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        req = self.svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        with open(dest, "wb") as fh:
            buf = io.BytesIO()
            dl = MediaIoBaseDownload(buf, req, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _, done = dl.next_chunk(num_retries=5)
                data = buf.getvalue()
                fh.write(data)
                h.update(data)
                buf.seek(0)
                buf.truncate()
        return dest, h.hexdigest()

    def export_text(self, file_id: str, max_chars: int = 20000) -> str:
        meta = self.metadata(file_id)
        mt = meta["mimeType"]
        if mt == "application/vnd.google-apps.document":
            data = self.svc.files().export(fileId=file_id, mimeType="text/plain").execute()
        elif mt.startswith("text/") or mt in ("application/json", "text/markdown"):
            data = self.svc.files().get_media(fileId=file_id).execute()
        else:
            raise ValueError(f"unsupported document type {mt}; convert to Google Doc or .txt/.md")
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
        return text[:max_chars]

    # ---- writes (callers must respect DRY_RUN)
    def move(self, file_id: str, to_key: str) -> None:
        meta = self.metadata(file_id)
        self.svc.files().update(fileId=file_id, addParents=self.settings.folder(to_key),
                                removeParents=",".join(meta["parents"]), fields="id,parents",
                                supportsAllDrives=True).execute()

    def upload(self, path: Path, folder_key: str, mime: str = "video/mp4", description: str = "") -> dict:
        from googleapiclient.http import MediaFileUpload

        body = {"name": path.name, "parents": [self.settings.folder(folder_key)],
                "description": description}
        media = MediaFileUpload(str(path), mimetype=mime, resumable=True, chunksize=8 * 1024 * 1024)
        return self.svc.files().create(body=body, media_body=media, fields=FILE_FIELDS,
                                       supportsAllDrives=True).execute()

    def ensure_structure(self) -> dict[str, str]:
        """Create YouTubeAutomation/ folder tree (only used by `init`)."""
        def find_or_create(name: str, parent: str | None) -> str:
            q = f"name = '{name}' and mimeType = '{FOLDER_MIME}' and trashed = false"
            q += f" and '{parent}' in parents" if parent else " and 'root' in parents"
            hits = self.svc.files().list(q=q, fields="files(id)").execute().get("files", [])
            if hits:
                return hits[0]["id"]
            body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent] if parent else []}
            return self.svc.files().create(body=body, fields="id").execute()["id"]

        ids: dict[str, str] = {}
        ids["root"] = find_or_create(FOLDER_NAMES["root"], None)
        ids["config"] = find_or_create(FOLDER_NAMES["config"], ids["root"])
        ids["inbox"] = find_or_create(FOLDER_NAMES["inbox"], ids["root"])
        for k in ("inbox_videos", "inbox_documents", "inbox_references"):
            ids[k] = find_or_create(FOLDER_NAMES[k], ids["inbox"])
        for k in ("references_images", "references_sfx", "references_music"):
            ids[k] = find_or_create(FOLDER_NAMES[k], ids["inbox_references"])
        for k in ("generated", "awaiting_approval", "published", "rejected", "archive"):
            ids[k] = find_or_create(FOLDER_NAMES[k], ids["root"])
        assert set(ids) == set(FOLDER_KEYS)
        self._ensure_keys_template(ids["config"])
        return ids

    def _ensure_keys_template(self, config_folder: str) -> None:
        """Create an EMPTY keys.json template (no values) if none exists."""
        import json
        from googleapiclient.http import MediaInMemoryUpload

        from .keys import TEMPLATE
        q = f"name = 'keys.json' and trashed = false and '{config_folder}' in parents"
        if self.svc.files().list(q=q, fields="files(id)").execute().get("files"):
            return
        media = MediaInMemoryUpload(json.dumps(TEMPLATE, indent=2).encode(), mimetype="application/json")
        self.svc.files().create(body={"name": "keys.json", "parents": [config_folder]},
                                media_body=media, fields="id").execute()
