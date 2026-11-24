"""Caption chunking and word timing.

Word times are distributed inside each scene's *measured* speech interval in proportion to
word length (+1 for the gap), which keeps captions within a scene in sync with the narration.
Optional Whisper alignment can replace these times later; Whisper is never used to generate voice.
Also emits an .srt file for accessibility/archive.
"""
from __future__ import annotations

import re

MAX_WORDS, MAX_CHARS = 4, 22


def word_times(text: str, start: float, end: float) -> list[tuple[str, float, float]]:
    ws = re.findall(r"\S+", text)
    if not ws:
        return []
    weights = [len(w) + 1 for w in ws]
    total, t, out = sum(weights), start, []
    for w, k in zip(ws, weights):
        d = (end - start) * k / total
        out.append((w, t, t + d))
        t += d
    return out


def chunk(words: list[tuple[str, float, float]]) -> list[dict]:
    chunks, cur = [], []
    for w in words:
        text = " ".join(x[0] for x in cur + [w])
        if cur and (len(cur) >= MAX_WORDS or len(text) > MAX_CHARS):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w[0][-1] in ".?!," and len(cur) >= 2:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return [{"start": c[0][1], "end": c[-1][2], "words": c} for c in chunks]


def build(scenes: list[dict], timings: list[dict]) -> list[dict]:
    out = []
    for s, t in zip(scenes, timings):
        out += chunk(word_times(s["narration"], t["speech_start"], t["speech_end"]))
    return out


def to_srt(caps: list[dict]) -> str:
    def ts(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{' '.join(w[0] for w in c['words'])}\n"
                     for i, c in enumerate(caps, 1))
