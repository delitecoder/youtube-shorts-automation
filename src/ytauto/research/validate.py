"""Validation of the researcher agent's JSON and topic de-duplication.

{
  "topic": "...", "topic_key": "kebab-case-unique-key", "category": "<one of ALLOWED_CATEGORIES>",
  "angle": "the story angle", "source_type": "research|drive_document",
  "sources": [{"title": "...", "url": "https://...", "publisher": "...", "published": "YYYY-MM-DD"}],
  "facts": [{"claim": "...", "source_urls": ["https://..."], "confidence": "high|medium|low"}]
}
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from ..config import ALLOWED_CATEGORIES

KEY_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validate_research(r: dict, today: date, history: list[dict], dedupe_days: int = 90) -> tuple[list[str], list[str]]:
    e, w = [], []
    for k in ("topic", "topic_key", "category", "angle", "sources", "facts"):
        if k not in r:
            e.append(f"missing '{k}'")
    if e:
        return e, w
    if r["category"] not in ALLOWED_CATEGORIES:
        e.append(f"category must be one of {ALLOWED_CATEGORIES}")
    if not KEY_RE.match(r["topic_key"]):
        e.append("topic_key must be kebab-case")
    is_doc = r.get("source_type") == "drive_document"
    if not is_doc and not 3 <= len(r["sources"]) <= 5:
        e.append("need 3–5 sources for researched topics")
    urls = set()
    for s in r["sources"]:
        if not str(s.get("url", "")).startswith("https://"):
            e.append(f"source url must be https: {s.get('url')}")
        urls.add(s.get("url"))
        try:
            pub = datetime.strptime(s.get("published", ""), "%Y-%m-%d").date()
            if pub > today:
                e.append(f"source dated in the future: {s.get('url')}")
        except ValueError:
            w.append(f"unknown publication date: {s.get('url')}")
    for f in r["facts"]:
        if not set(f.get("source_urls", [])) & urls and not is_doc:
            e.append(f"fact not backed by a listed source: {f.get('claim', '')[:60]}")
        if f.get("confidence") == "low":
            w.append(f"low-confidence fact should not be used: {f.get('claim', '')[:60]}")
    cutoff = today - timedelta(days=dedupe_days)
    for h in history:
        if h.get("topic_key") == r["topic_key"] and h.get("researched_at", "")[:10] >= cutoff.isoformat():
            e.append(f"topic '{r['topic_key']}' already covered on {h['researched_at'][:10]}")
    return e, w
