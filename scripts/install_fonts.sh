#!/usr/bin/env bash
# Installs the bundled fonts (Noto Sans Tamil, SIL OFL 1.1 — see renderer/fonts/OFL.txt) so the
# HyperFrames renderer (headless Chromium) can draw Tamil text. Idempotent; safe to run every time.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
dest="${FONT_DIR:-/usr/local/share/fonts/ytauto}"
if ! mkdir -p "$dest" 2>/dev/null; then dest="$HOME/.local/share/fonts/ytauto"; mkdir -p "$dest"; fi
cp -f "$here"/renderer/fonts/*.ttf "$dest"/
fc-cache -f "$dest" >/dev/null 2>&1 || true
if fc-list : family | grep -i "Noto Sans Tamil" >/dev/null; then echo "fonts ok: Noto Sans Tamil"
else echo "Noto Sans Tamil not found" >&2; exit 1; fi
