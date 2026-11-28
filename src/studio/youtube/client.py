"""YouTube Data API v3 wrapper: resumable upload with safe retries, status checks.

Notes verified against official docs (see docs/costs.md for links):
* Since 2026-11-02 (YouTube Data API revision history) uploads from unverified API projects are no
  longer forced to private, so no compliance audit is needed to publish. An audit is only needed
  for extra quota.
* publishAt is only valid with privacyStatus=private and must be in the future.
* There is no "Shorts" flag in the API: a video is classified as a Short by YouTube from its
  duration (<= 3 min) and square/vertical aspect ratio. Custom thumbnails for Shorts are not
  set via the API by this system.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path

RETRIABLE_STATUS = {500, 502, 503, 504}
MAX_RETRIES = 8


class UploadError(RuntimeError):
    pass


@dataclass
class UploadRequest:
    path: Path
    title: str
    description: str
    tags: list[str]
    category_id: str
    publish_at: str | None        # RFC3339 UTC, or None to stay private
    language: str = "en"
    made_for_kids: bool = False


def build_body(req: UploadRequest) -> dict:
    status = {"privacyStatus": "private", "selfDeclaredMadeForKids": req.made_for_kids,
              "embeddable": True, "license": "youtube"}
    if req.publish_at:
        status["publishAt"] = req.publish_at
    return {
        "snippet": {"title": req.title, "description": req.description, "tags": req.tags,
                    "categoryId": req.category_id, "defaultLanguage": req.language,
                    "defaultAudioLanguage": req.language},
        "status": status,
        # Since 2026-11-03 YouTube no longer assumes "no paid placement": declare it explicitly.
        "paidProductPlacementDetails": {"hasPaidProductPlacement": False},
    }


def run_resumable(request, sleep=time.sleep, log=print) -> dict:
    """Drive a googleapiclient resumable request to completion.

    Transient failures (5xx, connection errors) retry the *same* upload session with
    exponential backoff, so the server never sees two uploads. Any other error is raised.
    """
    import http.client
    import socket

    from googleapiclient.errors import HttpError

    response, attempt = None, 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                log(f"upload {int(status.progress() * 100)}%")
            attempt = 0
        except HttpError as e:
            if e.resp.status not in RETRIABLE_STATUS:
                raise UploadError(f"YouTube rejected the upload: HTTP {e.resp.status}") from e
            err = e
        except (http.client.HTTPException, socket.error, ConnectionError, TimeoutError) as e:
            err = e
        else:
            continue
        attempt += 1
        if attempt > MAX_RETRIES:
            raise UploadError(f"upload failed after {MAX_RETRIES} retries: {err}")
        sleep(min(2 ** attempt + random.random(), 64))
    if "id" not in response:
        raise UploadError(f"unexpected upload response: {response}")
    return response


class YouTube:
    def __init__(self, service):
        self.svc = service

    def upload(self, req: UploadRequest, log=print) -> dict:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(req.path), mimetype="video/*", resumable=True,
                                chunksize=8 * 1024 * 1024)
        request = self.svc.videos().insert(part="snippet,status,paidProductPlacementDetails", body=build_body(req),
                                           media_body=media, notifySubscribers=True)
        return run_resumable(request, log=log)

    def video_status(self, video_id: str) -> dict | None:
        items = self.svc.videos().list(part="status,snippet,contentDetails",
                                       id=video_id).execute().get("items", [])
        return items[0] if items else None

    def recent_uploads(self, limit: int = 25) -> list[dict]:
        ch = self.svc.channels().list(part="contentDetails", mine=True).execute()["items"][0]
        pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
        items = self.svc.playlistItems().list(part="contentDetails", playlistId=pl,
                                              maxResults=min(limit, 50)).execute().get("items", [])
        ids = [i["contentDetails"]["videoId"] for i in items]
        if not ids:
            return []
        return self.svc.videos().list(part="snippet,status,contentDetails,statistics",
                                      id=",".join(ids)).execute().get("items", [])
