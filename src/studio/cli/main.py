"""`python -m studio <command>`: every command prints one compact JSON object to stdout. No model calls here."""
from __future__ import annotations

import argparse
import sys

from studio.cli.commands import analytics, assets, planning, production, publishing, setup, state
from studio.cli.common import out
from studio.core.google_auth import MissingCredentials
from studio.orchestration.context import Ctx

GROUPS = (setup, planning, production, assets, publishing, analytics, state)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="studio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for g in GROUPS:
        g.register(sub)
    return ap


def handler(cmd: str):
    name = "cmd_" + cmd.replace("-", "_")
    for g in GROUPS:
        if hasattr(g, name):
            return getattr(g, name)
    raise KeyError(cmd)


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return handler(a.cmd)(Ctx(), a)
    except MissingCredentials as e:
        out({"ok": False, "error": str(e)})
        return 3


if __name__ == "__main__":
    sys.exit(main())
