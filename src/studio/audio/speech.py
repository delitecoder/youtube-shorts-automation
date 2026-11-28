"""Speech verification + word-level alignment with faster-whisper (MIT, CPU int8).

ASR is used as a pronunciation/content CHECK (word error rate against the approved script) and to time
captions from the actual speech; it never generates voice. English uses the `base` model; for Tamil the
ASR is not reliable enough to judge, so the check is reported as unknown (not passed) and captions keep
the labelled estimate.
"""
from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from functools import lru_cache

MODEL = {"en": "base.en"}


def available(lang: str) -> bool:
    if os.environ.get("STUDIO_ASR", "1") == "0" or lang not in MODEL:
        return False
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=2)
def _model(name: str):
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)


def _pcm16k(path):
    """Decode with ffmpeg to 16 kHz mono float32 (avoids faster-whisper's PyAV decoder/version coupling)."""
    import subprocess
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def transcribe(path, lang: str = "en") -> list[tuple[str, float, float]]:
    segs, _ = _model(MODEL[lang]).transcribe(_pcm16k(path), language=lang, word_timestamps=True, beam_size=1,
                                             vad_filter=False, condition_on_previous_text=False)
    return [(w.word.strip(), float(w.start), float(w.end)) for s in segs for w in (s.words or []) if w.word.strip()]


def norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


_NUM = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                         "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_NUM |= {"twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
         "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000"}
_UNITS = {"ms": ["milliseconds"], "gb": ["gigabytes"], "mb": ["megabytes"], "kb": ["kilobytes"], "s": ["seconds"],
          "percent": ["%"], "%": ["percent"]}


def tokens(text: str) -> list[str]:
    """Normalised words: number words -> digits, '12ms' -> '12 milliseconds', spelled acronyms merged
    ('A W S' == 'AWS')."""
    out = []
    for w in re.findall(r"\S+", text):
        m = re.fullmatch(r"(\d+(?:\.\d+)?)([a-zA-Z%]+)", re.sub(r"[^\w%.]", "", w))
        if m and m.group(2).lower() in _UNITS:
            out += [m.group(1), _UNITS[m.group(2).lower()][0]]
            continue
        n = norm(w)
        if n:
            out.append(_NUM.get(n, n))
    merged, buf = [], ""
    for t in out:
        if len(t) == 1 and t.isalpha():
            buf += t
            continue
        if buf:
            merged.append(buf)
            buf = ""
        merged.append(t)
    if buf:
        merged.append(buf)
    return merged


def same(a: str, b: str) -> bool:
    """ASR spells technical words its own way ('jayson'/'jason'): near-identical tokens count as a match."""
    return a == b or (len(a) > 3 and len(b) > 3 and SequenceMatcher(a=a, b=b).ratio() >= 0.8)


def wer(reference: str, hypothesis: str) -> float:
    r, h = tokens(reference), tokens(hypothesis)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (not same(r[i - 1], h[j - 1])))
            prev = cur
    return d[len(h)] / len(r)


def align(script_words: list[str], asr: list[tuple[str, float, float]]) -> list[tuple[str, float, float] | None]:
    """Give each SCRIPT word the time of the matching spoken word (sequence alignment); None when unmatched."""
    a = [norm(w) for w in script_words]
    b = [norm(w[0]) for w in asr]
    out: list = [None] * len(script_words)
    for blk in SequenceMatcher(a=a, b=b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            w = asr[blk.b + k]
            out[blk.a + k] = (script_words[blk.a + k], w[1], w[2])
    return out
