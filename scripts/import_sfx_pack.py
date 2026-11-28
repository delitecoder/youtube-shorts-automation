#!/usr/bin/env python3
"""Build an SFX library (manifest + 48 kHz mono WAVs) from a downloaded SFX pack, e.g. the free
Earshot SFX collection by Paul Urmson (itch.io). Run on your computer after extracting the pack:

    python scripts/import_sfx_pack.py --pack ~/Downloads/earshot --out sfx_library \
        --source "Earshot SFX (Paul Urmson)" --license-file ~/Downloads/earshot/License.txt

Then upload the *contents* of sfx_library/ to Drive 01_Inbox/References/SFX and run
`python -m studio sync-library`. Read the pack's licence yourself first: this tool records it,
it does not grant any rights. Files are picked by keyword per category, trimmed to 2.5 s.
"""
import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

KEYWORDS = {
    "whoosh": r"whoosh|swish|swoosh|pass[ _-]?by|sweep",
    "pop": r"\bpop|bubble|blip",
    "tick": r"tick|click|tap",
    "chime": r"chime|bell|ding|notification",
    "riser": r"riser|rise|build[ _-]?up|swell",
    "impact": r"impact|hit|thud|boom|slam",
}
AUDIO = {".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--license-file", required=True)
    ap.add_argument("--per-category", type=int, default=4)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    lic = Path(a.license_file)
    shutil.copy(lic, out / "LICENSE-sfx.txt")
    files = sorted(p for p in Path(a.pack).rglob("*") if p.suffix.lower() in AUDIO)
    assets, used = [], set()
    for cat, rx in KEYWORDS.items():
        picked = [p for p in files if re.search(rx, p.stem, re.I) and p not in used][: a.per_category]
        for i, p in enumerate(picked):
            name = f"{cat}_{i + 1:02d}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-t", "2.5", "-ac", "1",
                            "-ar", "48000", "-af", "afade=t=out:st=2.2:d=0.3", str(out / name)], check=True)
            assets.append({"file": name, "category": cat, "source": a.source,
                           "license": f"see LICENSE-sfx.txt ({lic.name})", "original": p.name})
            used.add(p)
    (out / "manifest.json").write_text(json.dumps({"assets": assets}, indent=1))
    print(f"{len(assets)} SFX written to {out} (categories: {sorted({x['category'] for x in assets})})")


if __name__ == "__main__":
    main()
