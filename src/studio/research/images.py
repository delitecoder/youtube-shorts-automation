"""Licensed images from the internet (plus your own from Drive) for `image` scenes.

Sources: Wikimedia Commons (primary: rich licence metadata, high-res, two hosts) and Openverse
(secondary). Only licences that allow commercial use AND modification without share-alike are
accepted: CC0, Public Domain / PDM, CC BY (any version). CC BY-SA, NC and ND are rejected.
Every accepted image gets a credit line that `submit` adds to the YouTube description.

Token budget: Claude never looks at candidate images. Each candidate is checked locally by the
vision model (OmniVision / OmniVLM-968M via production/vision.py) which returns a one-line
caption plus yes/no flags; selection is deterministic Python.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path

import os
import time

# Wikimedia asks API clients to identify themselves with contact details. Set IMAGE_API_CONTACT
# (e.g. a project URL or a mailbox you choose) to be treated as a well-behaved client; anonymous
# traffic from shared cloud IPs is often rate-limited (HTTP 429).
UA = {"User-Agent": "ytauto/0.1 (educational video tool; respects licences"
                    + (f"; {os.environ['IMAGE_API_CONTACT']}" if os.environ.get("IMAGE_API_CONTACT") else "")
                    + ")"}


def _get(session, url, params, retries: int = 2, sleep=time.sleep):
    """GET with polite retries on 429/5xx, honouring Retry-After (max 30 s)."""
    for attempt in range(retries + 1):
        r = session.get(url, params=params, headers=UA, timeout=30)
        if r.status_code not in (429, 500, 502, 503, 504) or attempt == retries:
            r.raise_for_status()
            return r
        try:
            wait = min(float(r.headers.get("Retry-After", 5)), 30)
        except ValueError:
            wait = 5
        sleep(wait)
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"
MAX_BYTES = 15 * 1024 * 1024
MIN_SHORT_SIDE = 720
OK_MIME = {"image/jpeg", "image/png", "image/webp"}


@dataclass
class Candidate:
    source: str
    title: str
    url: str            # downloadable (already resized where the API allows)
    page_url: str
    license: str
    creator: str
    width: int
    height: int
    mime: str = "image/jpeg"
    caption: str = ""
    score: float = 0.0
    file: str = ""
    visual_unverified: bool = False
    vision: dict | None = None

    @property
    def credit(self) -> str:
        who = self.creator or "unknown author"
        return f'"{self.title}" by {who}, {self.license}, via {self.source} ({self.page_url})'

    @property
    def requires_permission(self) -> bool:
        return not license_open(self.license)

    @property
    def needs_attribution(self) -> bool:
        return not license_is_public_domain(self.license)


def _strip(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


LICENSE_MODE = os.environ.get("IMAGE_LICENSE_MODE", "open")   # open | owner_approval


def license_allowed(lic: str, mode: str | None = None) -> bool:
    """open: CC0 / PD / CC BY only. owner_approval: additionally any other licence with a known
    creator (CC BY-SA, NC, ND, all rights reserved) — such images are marked requires_permission,
    listed in the approval email, and must be explicitly approved by the owner."""
    if (mode or LICENSE_MODE) == "owner_approval" and lic and "fair use" not in lic.lower():
        return True
    return license_open(lic)


def license_open(lic: str) -> bool:
    l = (lic or "").lower().replace("_", " ").replace("-", " ").strip()
    if any(x in l for x in (" sa", "share", " nc", "noncommercial", " nd", "noderiv", "fair use", "copyright")):
        return False
    return license_is_public_domain(lic) or bool(re.match(r"^cc by( \d(\.\d)?)?$", l))


def license_is_public_domain(lic: str) -> bool:
    l = (lic or "").lower().replace("-", " ").strip()
    return l.startswith("cc0") or l in ("pdm", "pd", "public domain") or l.startswith("public domain")


# ------------------------------------------------------------------ search
def search_commons(query: str, session, limit: int = 15) -> list[Candidate]:
    params = {"action": "query", "format": "json", "generator": "search",
              "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit,
              "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600}
    r = _get(session, COMMONS_API, params)
    out = []
    for page in (r.json().get("query", {}).get("pages", {}) or {}).values():
        ii = (page.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata", {})
        lic = _strip(md.get("LicenseShortName", {}).get("value", ""))
        out.append(Candidate(
            source="Wikimedia Commons", title=_strip(md.get("ObjectName", {}).get("value", "")) or page.get("title", ""),
            url=ii.get("thumburl") or ii.get("url", ""), page_url=ii.get("descriptionurl", ""),
            license=lic, creator=_strip(md.get("Artist", {}).get("value", ""))[:120],
            width=int(ii.get("width", 0)), height=int(ii.get("height", 0)), mime=ii.get("mime", "")))
    return out


def search_openverse(query: str, session, limit: int = 15) -> list[Candidate]:
    params = {"q": query, "page_size": limit, "mature": "false"}
    if LICENSE_MODE != "owner_approval":
        params["license"] = "cc0,pdm,by"
    r = _get(session, OPENVERSE_API, params)
    out = []
    for it in r.json().get("results", []):
        lic = f"CC {it.get('license', '').upper()} {it.get('license_version', '')}".strip()
        if it.get("license") in ("cc0", "pdm"):
            lic = "CC0" if it["license"] == "cc0" else "Public Domain Mark"
        out.append(Candidate(
            source=f"Openverse/{it.get('source', '')}", title=it.get("title", "") or "untitled",
            url=it.get("url", ""), page_url=it.get("foreign_landing_url", ""), license=lic,
            creator=(it.get("creator") or "")[:120], width=int(it.get("width") or 0),
            height=int(it.get("height") or 0), mime="image/" + (it.get("filetype") or "jpeg")))
    return out


def filter_candidates(cands: list[Candidate]) -> list[Candidate]:
    seen, out = set(), []
    for c in cands:
        if not c.url.startswith("https://") or c.url in seen:
            continue
        if not license_allowed(c.license):
            continue
        if c.requires_permission and not c.creator.strip():
            continue                       # never use a restricted image without a named creator
        if c.mime and c.mime not in OK_MIME:
            continue
        if c.width and c.height and min(c.width, c.height) < MIN_SHORT_SIDE:
            continue
        seen.add(c.url)
        out.append(c)
    return out


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


def relevance(query: str, text: str) -> float:
    q = words(query)
    return len(q & words(text)) / max(len(q), 1)


# ------------------------------------------------------------------ download + choose
def download(c: Candidate, dest: Path, session) -> str:
    from PIL import Image

    with session.get(c.url, headers=UA, timeout=60, stream=True) as r:
        r.raise_for_status()
        h, size = hashlib.sha256(), 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(256 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("image too large")
                fh.write(chunk)
                h.update(chunk)
    with Image.open(dest) as im:          # validates the file really is an image
        im.verify()
    return h.hexdigest()


def choose(query: str, cands: list[Candidate], workdir: Path, name: str, session, describer,
           max_checks: int = 4, detector=None) -> tuple[Candidate | None, list[str]]:
    """Rank by metadata, then let the local vision model verify the top few."""
    notes: list[str] = []
    for c in cands:
        c.score = relevance(query, c.title)
    ranked = sorted(cands, key=lambda c: c.score, reverse=True)[:max_checks]
    tmpdir = workdir / "assets" / "_candidates"
    tmpdir.mkdir(parents=True, exist_ok=True)
    best, best_path = None, None
    for i, c in enumerate(ranked):
        p = tmpdir / f"{name}_{i}{Path(c.url.split('?')[0]).suffix or '.jpg'}"
        try:
            download(c, p, session)
        except Exception as e:  # noqa: BLE001
            notes.append(f"download failed: {c.page_url}: {str(e)[:80]}")
            continue
        v = describer.describe(p, query)
        if detector is not None:
            try:
                det = detector.detect(p, query)
                v["focus"], v["boxes"] = det["focus"], det["boxes"]
                v["logo"] = bool(v.get("logo")) or det["logo"]
                v["watermark"] = bool(v.get("watermark")) or det["watermark"]
            except Exception as e:  # noqa: BLE001 - detector is an optional extra
                notes.append(f"detector failed: {str(e)[:80]}")
        c.visual_unverified = bool(v.get("visual_unverified"))
        c.vision = v
        if v.get("watermark") or v.get("logo") or v.get("face"):
            notes.append(f"rejected by vision check ({v}): {c.page_url}")
            continue
        c.caption = v.get("caption", "")
        rel = v.get("relevance")
        if rel is None and c.caption:
            rel = relevance(query, c.caption)
        if rel is not None:
            if rel < 0.15:
                notes.append(f"rejected as irrelevant (rel {rel:.2f}): {c.page_url}")
                continue
            c.score = 0.3 * c.score + 0.7 * rel
        if c.requires_permission:
            c.score -= 0.25                # prefer openly licensed images when they are as good
        if best is None or c.score > best.score:
            best, best_path = c, p
    if best:
        final = workdir / "assets" / f"{name}{best_path.suffix}"
        best_path.replace(final)
        best.file = final.name
        notes.append(f"chosen: {final.name}")
        return best, notes
    return None, notes
