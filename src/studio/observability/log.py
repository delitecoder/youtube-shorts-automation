"""Structured JSON logs. One line per event to stderr and runtime/logs/<run_id>.jsonl.

Fields: ts, level, event, run_id, video_id, stage, error_category, plus free keyword fields.
Secret-looking values (keys named *token*, *secret*, *password*, *key*) are redacted before writing.
"""
from __future__ import annotations

import json
import os
import sys
from enum import Enum
from pathlib import Path

from studio.core.config import RUNTIME_DIR
from studio.core.state import iso, utcnow


class ErrorCategory(str, Enum):
    CONFIG = "config"
    RESOURCE = "resource"
    EXTERNAL_API = "external_api"
    VALIDATION = "validation"
    QC = "qc"
    RENDER = "render"
    AUDIO = "audio"
    STATE = "state"
    SECURITY = "security"
    UNKNOWN = "unknown"


_SECRETISH = ("token", "secret", "password", "passwd", "api_key", "apikey", "credential", "private_key")
_CTX: dict = {}


def bind(**ctx) -> None:
    """Set run-wide context (run_id, video_id, stage) attached to every later event."""
    _CTX.update({k: v for k, v in ctx.items() if v is not None})


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("[redacted]" if any(s in str(k).lower() for s in _SECRETISH) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def log(event: str, level: str = "info", error_category: ErrorCategory | str | None = None, **fields) -> dict:
    rec = {"ts": iso(utcnow()), "level": level, "event": event, **_CTX, **fields}
    if error_category:
        rec["error_category"] = ErrorCategory(error_category).value
    rec = redact(rec)
    line = json.dumps(rec, default=str, ensure_ascii=False)
    if os.environ.get("STUDIO_LOG_STDERR", "1") != "0":
        print(line, file=sys.stderr)
    run = rec.get("run_id")
    if run:
        d = Path(RUNTIME_DIR) / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"{run}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return rec
