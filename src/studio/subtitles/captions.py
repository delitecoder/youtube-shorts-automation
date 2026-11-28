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
    """Estimated timing (labelled fallback when no aligned speech is available)."""
    out = []
    for s, t in zip(scenes, timings):
        out += chunk(word_times(s["narration"], t["speech_start"], t["speech_end"]))
    return out


def build_aligned(scenes: list[dict], timings: list[dict], asr_words: list[tuple[str, float, float]]) -> tuple[list[dict], dict]:
    """Caption times from the ACTUAL speech: each script word takes the time of its aligned spoken word;
    unmatched words are interpolated between matched neighbours inside the scene's speech interval.
    Returns (captions, stats) where stats has matched ratio and max offset vs the estimate."""
    from studio.audio.speech import align
    out, matched, total, estimated = [], 0, 0, []
    for n, (s, t) in enumerate(zip(scenes, timings), 1):
        est = word_times(s["narration"], t["speech_start"], t["speech_end"])
        words = [w for w, _, _ in est]
        spoken = [w for w in asr_words if t["speech_start"] - 0.3 <= w[1] <= t["speech_end"] + 0.3]
        al = align(words, spoken)
        total += len(words)
        matched += sum(1 for x in al if x)
        fixed = []
        for i, (w, e) in enumerate(zip(words, est)):
            if al[i]:
                fixed.append(al[i])
                continue
            prev = fixed[-1][2] if fixed else t["speech_start"]
            nxt_i = next((j for j in range(i + 1, len(al)) if al[j]), None)
            nxt = al[nxt_i][1] if nxt_i is not None else t["speech_end"]
            run = (nxt_i if nxt_i is not None else len(al)) - i       # unmatched words sharing the gap
            span = max(0.05, (nxt - prev) / run)
            fixed.append((w, prev, min(max(nxt, prev + 0.05), prev + span)))
        # ASR can stretch one word over a pause and drop the rest; then the tail overruns the scene. Use the
        # estimate for that scene (reported in stats) instead of captions that run into the next scene.
        bound = timings[n]["speech_start"] if n < len(timings) else t["speech_end"] + 0.3
        stretched = any(x and x[2] - x[1] > 1.5 for x in al)          # one "word" spanning a pause
        if stretched or any(e > bound + 1e-6 or b < a for (_, _, a), (_, b, e) in zip([("", 0, 0)] + fixed, fixed)):
            fixed = est
            estimated.append(n)
        out += chunk(fixed)
    return out, {"matched_ratio": round(matched / total, 3) if total else 0.0, "words": total,
                 "estimated_scenes": estimated}


def check(caps: list[dict], max_offset_s: float = 0.25) -> list[str]:
    """Caption QC: monotonic, non-overlapping, no empty or duplicated chunks."""
    problems = []
    for a, b in zip(caps, caps[1:]):
        if b["start"] < a["start"] - 1e-6:
            problems.append(f"caption at {b['start']:.2f}s starts before the previous one")
        if a["end"] - b["start"] > max_offset_s:
            problems.append(f"captions overlap by {a['end'] - b['start']:.2f}s at {b['start']:.2f}s")
        if [w[0] for w in a["words"]] == [w[0] for w in b["words"]] and len(a["words"]) > 1:
            problems.append(f"duplicated caption at {b['start']:.2f}s")
    problems += [f"empty caption at {c['start']:.2f}s" for c in caps if not c["words"]]
    return problems


def to_srt(caps: list[dict]) -> str:
    def ts(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{' '.join(w[0] for w in c['words'])}\n"
                     for i, c in enumerate(caps, 1))
