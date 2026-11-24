"""Single organic Bluesky post after a Short is confirmed public (PROMOTION_ENABLED only).

Uses an app password (BLUESKY_APP_PASSWORD, from a protected secret), never the main password.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

PDS = "https://bsky.social/xrpc"


def build_post(title: str, url: str, hashtags: list[str]) -> tuple[str, list[dict]]:
    tags = " ".join(f"#{t.lstrip('#')}" for t in hashtags[:3])
    text = f"New Short: {title}\n\n{url}"
    if tags and len(text) + len(tags) + 2 <= 300:
        text += f"\n\n{tags}"
    if len(text) > 300:
        raise ValueError("Bluesky post exceeds 300 characters")
    b = text.encode("utf-8")
    start = b.index(url.encode())
    facets = [{"index": {"byteStart": start, "byteEnd": start + len(url.encode())},
               "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]}]
    return text, facets


def post(handle: str, title: str, url: str, hashtags: list[str], session=None) -> str:
    import requests

    s = session or requests.Session()
    pw = os.environ.get("BLUESKY_APP_PASSWORD", "")
    if not (handle and pw):
        raise RuntimeError("BLUESKY_HANDLE / BLUESKY_APP_PASSWORD not configured")
    auth = s.post(f"{PDS}/com.atproto.server.createSession",
                  json={"identifier": handle, "password": pw}, timeout=30)
    auth.raise_for_status()
    a = auth.json()
    text, facets = build_post(title, url, hashtags)
    rec = {"$type": "app.bsky.feed.post", "text": text, "facets": facets, "langs": ["en"],
           "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    r = s.post(f"{PDS}/com.atproto.repo.createRecord",
               headers={"Authorization": f"Bearer {a['accessJwt']}"},
               json={"repo": a["did"], "collection": "app.bsky.feed.post", "record": rec}, timeout=30)
    r.raise_for_status()
    return r.json()["uri"]
