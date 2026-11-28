"""YAML configuration in config/*.yaml (non-secret defaults). Cached; never contains secrets or protected flags."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from studio.core.config import ROOT

CONFIG_DIR = ROOT / "config"
PROTECTED = {"DRY_RUN", "UPLOAD_ENABLED", "PUBLICATION_ENABLED", "PROMOTION_ENABLED"}


@lru_cache(maxsize=None)
def load(name: str, base: Path | None = None) -> dict:
    path = (base or CONFIG_DIR) / f"{name}.yaml"
    data = yaml.safe_load(path.read_text()) or {}
    _reject_protected(data, name)
    return data


def _reject_protected(node, name: str) -> None:
    """Protected flags are environment-only: a YAML file must never set them."""
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).upper() in PROTECTED:
                raise ValueError(f"config/{name}.yaml must not set protected flag {k}")
            _reject_protected(v, name)
    elif isinstance(node, list):
        for v in node:
            _reject_protected(v, name)
