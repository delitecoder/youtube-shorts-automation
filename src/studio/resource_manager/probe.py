"""Inspect the real execution environment and decide what work may start.

Limits come from config/resources.yaml. Worker count is limited by MEASURED free memory first,
then CPU count, then the configured maximum.
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess

import psutil

from studio.core import conf
from studio.core.config import ROOT

CREDENTIAL_ENV = ("YTA_PIPELINE_OAUTH", "SPREADSHEET_ID", "DRIVE_FOLDER_IDS", "GOOGLE_TTS_API_KEY")


class ResourceError(RuntimeError):
    pass


def _version(cmd: list[str]) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.splitlines()[0][:80]
    except Exception:  # noqa: BLE001
        return "present (version unknown)"


def _network(host: str = "www.googleapis.com") -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def probe(check_network: bool = True) -> dict:
    vm = psutil.virtual_memory()
    du = shutil.disk_usage(ROOT)
    return {
        "ram_total_gb": round(vm.total / 1e9, 2), "ram_available_gb": round(vm.available / 1e9, 2),
        "cpu_count": os.cpu_count(), "disk_free_gb": round(du.free / 1e9, 2),
        "python": platform.python_version(),
        "ffmpeg": _version(["ffmpeg", "-version"]), "blender": _version(["blender", "--version"]),
        "node": _version(["node", "--version"]),
        "gpu": bool(shutil.which("nvidia-smi")),
        "network": _network() if check_network else None,
        "credentials_present": {k: bool(os.environ.get(k)) for k in CREDENTIAL_ENV},   # names only
    }


def tree_rss_gb() -> float:
    p = psutil.Process()
    total = p.memory_info().rss
    for c in p.children(recursive=True):
        try:
            total += c.memory_info().rss
        except psutil.Error:
            pass
    return total / 1e9


def workers(cfg: dict | None = None, available_gb: float | None = None) -> int:
    c = cfg or conf.load("resources")
    avail = available_gb if available_gb is not None else psutil.virtual_memory().available / 1e9
    by_mem = int((avail - c["memory"]["min_free_gb"]) // c["memory"]["per_worker_gb"])
    return max(1, min(by_mem, os.cpu_count() or 1, c["cpu"]["max_workers"]))


def guard(step: str, need_gb: float = 0.0, need_disk_gb: float = 0.0, cfg: dict | None = None,
          available_gb: float | None = None, disk_free_gb: float | None = None, rss_gb: float | None = None) -> None:
    """Raise ResourceError before starting a heavy step that the measured environment cannot afford."""
    c = cfg or conf.load("resources")
    avail = available_gb if available_gb is not None else psutil.virtual_memory().available / 1e9
    free = disk_free_gb if disk_free_gb is not None else shutil.disk_usage(ROOT).free / 1e9
    rss = rss_gb if rss_gb is not None else tree_rss_gb()
    if avail - need_gb < c["memory"]["min_free_gb"]:
        raise ResourceError(f"{step}: needs {need_gb} GB, only {avail:.1f} GB free RAM")
    if rss + need_gb > c["memory"]["app_working_set_gb"]:
        raise ResourceError(f"{step}: working set would exceed {c['memory']['app_working_set_gb']} GB")
    if free - need_disk_gb < c["disk"]["min_free_gb"]:
        raise ResourceError(f"{step}: needs {need_disk_gb} GB disk, only {free:.1f} GB free")
