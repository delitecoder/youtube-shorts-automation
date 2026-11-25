"""Runtime settings. Everything comes from environment variables; secrets never live in the repo.

Safety defaults: DRY_RUN=true and every *_ENABLED flag false. A flag is only "on" when its
value is exactly "true" (case-insensitive); anything else, including typos, means off.
DRY_RUN is the reverse: it is only off when its value is exactly "false".
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = Path(os.environ.get("YTA_RUNTIME_DIR") or ROOT / "runtime")

ALLOWED_CATEGORIES = (
    "educational technology",
    "artificial intelligence",
    "emerging technology",
    "programming",
    "software engineering",
    "technology news",
    "general education",
    "tech stories",
    "motivation",
    "system design",
)

# Daily content theme, by weekday in the channel timezone (Monday = 0). `discover` reports the
# day's theme; the researcher picks a topic that fits it and the storywriter adapts the story arc.
CONTENT_THEMES = {
    "trending_search": {
        "label": "Trending Tech",
        "brief": "the tech topic people are searching for most right now (Google Trends, YouTube "
                 "search trends, trending products/repos this week), explained simply",
    },
    "tech_story": {
        "label": "Tech Story",
        "brief": "a true, little-known story behind a technology, product, famous bug, invention "
                 "or company decision, told as a story with a twist",
    },
    "trending_news": {
        "label": "Tech News",
        "brief": "the most important tech news of the last 7 days and why it matters to viewers",
    },
    "motivation": {
        "label": "Tech Motivation",
        "brief": "a real, verifiable story of a founder, engineer, student or team in tech who "
                 "overcame failure, ending with one practical lesson for the viewer",
    },
}
# Shorts: daily tech news (owner decision 2026-11-25; never repeat a story: ResearchHistory
# topic_keys are passed to the researcher). A ThemePlan row still overrides a single day.
CONTENT_ROTATION = ("trending_news",) * 7                               # Mon … Sun

# Long-form (16:9, 6–8 min) daily system-design explainers, taught in this order; `discover
# --format long` returns the first topic not yet in ResearchHistory. Owner can override per day
# with a ThemePlan row whose theme starts with "long:" (e.g. "long: Design WhatsApp").
SYSTEM_DESIGN_SYLLABUS = (
    "What happens when you type a URL and press Enter",
    "Client-server architecture and APIs",
    "Vertical vs horizontal scaling",
    "Load balancers",
    "Caching and cache invalidation",
    "CDNs: how Netflix and YouTube serve video fast",
    "Database indexing",
    "SQL vs NoSQL: how to choose",
    "Database replication",
    "Sharding and partitioning",
    "Consistent hashing",
    "CAP theorem in practice",
    "Message queues and event-driven systems",
    "Rate limiting",
    "API gateways and reverse proxies",
    "Microservices vs monolith",
    "Design a URL shortener",
    "Design a rate limiter",
    "Design WhatsApp chat",
    "Design Instagram feed",
    "Design YouTube video upload and streaming",
    "Design Uber ride matching",
    "Design a notification system",
    "Design Google Docs real-time collaboration",
    "Design a web crawler",
    "Design a distributed key-value store",
    "Design a payment system",
    "Design Twitter/X timeline",
    "Design a search autocomplete",
    "Design Netflix recommendations at scale",
)
LONG_THEME = {"key": "system_design", "label": "System Design",
              "brief": "a system-design explainer for beginners-to-intermediate engineers: real-world "
                       "hook, the problem, the naive design, why it breaks at scale, then the real "
                       "design step by step with diagrams, trade-offs and a recap"}


def theme_for(day, plan: list[dict] | None = None) -> dict:
    """The content theme for a date (a datetime.date in the channel timezone).

    `plan` = rows of the ThemePlan sheet tab (date, theme, details). A row for this date wins:
    its theme may be a theme key/label ("motivation", "Tech Story") or any free text such as
    "AI in healthcare"; `details` is passed to the researcher as extra direction."""
    for row in reversed(plan or []):                       # the last row for a date wins
        if _plan_date(row.get("date", "")) != day or not row.get("theme", "").strip():
            continue
        text, details = row["theme"].strip(), row.get("details", "").strip()
        norm = text.lower().replace(" ", "_").replace("-", "_")
        for key, t in CONTENT_THEMES.items():
            if norm in (key, t["label"].lower().replace(" ", "_")):
                brief = t["brief"] + (f". Owner direction: {details}" if details else "")
                return {"key": key, "label": t["label"], "brief": brief, "source": "ThemePlan"}
        brief = f"owner-chosen topic/theme: {text}" + (f". Details: {details}" if details else "")
        return {"key": "custom", "label": text[:28], "brief": brief, "source": "ThemePlan"}
    key = CONTENT_ROTATION[day.weekday()]
    return {"key": key, **CONTENT_THEMES[key], "source": "rotation"}


def _plan_date(s: str):
    """Accepts 2026-11-25, 25/11/2026, 25-11-2026, 25.11.2026 (day first, as typed in India)."""
    from datetime import datetime as _dt

    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return _dt.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

FOLDER_KEYS = (
    "root", "config", "inbox", "inbox_videos", "inbox_documents", "inbox_references",
    "references_images", "references_sfx", "references_music",
    "generated", "awaiting_approval", "published", "rejected", "archive",
)
FOLDER_NAMES = {
    "root": "YouTubeAutomation",
    "config": "00_Config",           # keys.json lives here (private, never shared)
    "inbox": "01_Inbox",
    "inbox_videos": "Videos",
    "inbox_documents": "Documents",
    "inbox_references": "References",
    "references_images": "Images",   # your own / licensed images (RIGHTS: … in description)
    "references_sfx": "SFX",         # e.g. selected Earshot SFX + manifest.json
    "references_music": "Music",     # e.g. ACE-Step beds you generated + manifest.json
    "generated": "02_Generated",
    "awaiting_approval": "03_Awaiting_Approval",
    "published": "04_Published",
    "rejected": "05_Rejected",
    "archive": "Archive",            # uploaded videos are moved here after a successful upload
}


def _on(v: str | None) -> bool:
    return (v or "").strip().lower() == "true"


def _dry(v: str | None) -> bool:
    return (v or "").strip().lower() != "false"


TTS_FREE_TIER_CAP = 900_000   # Chirp 3 HD: first 1,000,000 characters/month free (pricing page, 2026-11)
NARRATION_CHAR_LIMIT = {"short": 1_000, "long": 8_000}   # per video, spaces included


@dataclass(frozen=True)
class Settings:
    dry_run: bool = True
    upload_enabled: bool = False
    publication_enabled: bool = False
    promotion_enabled: bool = False
    approver_email: str = ""
    approver_is_owner: bool = True          # approver mailbox == mailbox running Apps Script
    timezone: str = "Asia/Kolkata"
    publish_time: str = "19:30"
    min_lead_minutes: int = 120              # publishAt must be at least this far in the future
    approval_ttl_hours: int = 24
    publish_window_hours: int = 24           # approved item must be uploaded within this window
    tts_monthly_char_budget: int = 600_000   # Chirp 3 HD free tier = 1M chars/month (spaces count)
    tts_voice: str = "en-US-Chirp3-HD-Charon"
    tts_language: str = "en-US"
    tts_provider: str = "google"             # google | kokoro | edge | piper | tone (tests only)
    tts_fallbacks: tuple = ("kokoro", "piper")  # tried in order if the primary fails
    allow_edge_tts: bool = False             # Edge TTS is an unofficial service: opt-in only
    kokoro_voice: str = "am_michael"
    edge_voice: str = "en-US-AndrewNeural"
    music_enabled: bool = True
    sfx_enabled: bool = True
    outro_enabled: bool = True
    outro_text: str = "If you learned something, subscribe for a new tech story every day."
    long_publish_time: str = "17:30"         # long-form slot (Shorts: publish_time / peak-time)
    long_outro_text: str = ("If this made system design click for you, subscribe. "
                            "A new breakdown drops every day.")
    max_source_video_seconds: float = 180.0
    max_source_video_bytes: int = 2 * 1024**3
    spreadsheet_id: str = ""
    folders: Mapping[str, str] = field(default_factory=dict)
    publisher_verify_gmail: bool = True
    youtube_category_id: str = "27"          # Education
    channel_language: str = "en"
    bluesky_handle: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        folders = {}
        if e.get("DRIVE_FOLDER_IDS"):
            folders = json.loads(e["DRIVE_FOLDER_IDS"])
            unknown = set(folders) - set(FOLDER_KEYS)
            if unknown:
                raise ValueError(f"Unknown DRIVE_FOLDER_IDS keys: {sorted(unknown)}")
        return cls(
            dry_run=_dry(e.get("DRY_RUN")),
            upload_enabled=_on(e.get("UPLOAD_ENABLED")),
            publication_enabled=_on(e.get("PUBLICATION_ENABLED")),
            promotion_enabled=_on(e.get("PROMOTION_ENABLED")),
            approver_email=e.get("APPROVER_EMAIL", "").strip().lower(),
            approver_is_owner=e.get("APPROVER_IS_OWNER", "true").strip().lower() != "false",
            timezone=e.get("PUBLISH_TIMEZONE", "Asia/Kolkata"),
            publish_time=e.get("PUBLISH_TIME", "19:30"),
            approval_ttl_hours=int(e.get("APPROVAL_TTL_HOURS", "24")),
            # never above 90 % of Google's free tier, so voice can never become a paid cost
            tts_monthly_char_budget=min(int(e.get("TTS_MONTHLY_CHAR_BUDGET", "600000")), TTS_FREE_TIER_CAP),
            tts_voice=e.get("TTS_VOICE", "en-US-Chirp3-HD-Charon"),
            tts_language=e.get("TTS_LANGUAGE", "en-US"),
            tts_provider=e.get("TTS_PROVIDER", "google"),
            tts_fallbacks=tuple(x.strip() for x in e.get("TTS_FALLBACKS", "kokoro,piper").split(",") if x.strip()),
            allow_edge_tts=_on(e.get("ALLOW_EDGE_TTS")),
            kokoro_voice=e.get("KOKORO_VOICE", "am_michael"),
            edge_voice=e.get("EDGE_VOICE", "en-US-AndrewNeural"),
            music_enabled=e.get("MUSIC_ENABLED", "true").lower() != "false",
            sfx_enabled=e.get("SFX_ENABLED", "true").lower() != "false",
            outro_enabled=e.get("OUTRO_ENABLED", "true").lower() != "false",
            outro_text=e.get("OUTRO_TEXT", cls.outro_text),
            long_publish_time=e.get("LONG_PUBLISH_TIME", "17:30"),
            spreadsheet_id=e.get("SPREADSHEET_ID", ""),
            folders=folders,
            publisher_verify_gmail=e.get("PUBLISHER_VERIFY_GMAIL", "true").lower() != "false",
            youtube_category_id=e.get("YOUTUBE_CATEGORY_ID", "27"),
            bluesky_handle=e.get("BLUESKY_HANDLE", ""),
        )

    @property
    def external_writes_allowed(self) -> bool:
        return not self.dry_run

    def folder(self, key: str) -> str:
        if key not in FOLDER_KEYS:
            raise KeyError(key)
        fid = self.folders.get(key, "")
        if not fid:
            raise RuntimeError(f"Drive folder '{key}' not configured (DRIVE_FOLDER_IDS)")
        return fid

    def describe_flags(self) -> dict:
        return {
            "DRY_RUN": self.dry_run,
            "UPLOAD_ENABLED": self.upload_enabled,
            "PUBLICATION_ENABLED": self.publication_enabled,
            "PROMOTION_ENABLED": self.promotion_enabled,
        }
