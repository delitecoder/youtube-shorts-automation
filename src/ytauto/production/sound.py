"""Sound design: sound effects, background music and the final mix.

Sources, in order of preference:
1. Asset libraries you control (Drive 01_Inbox/References/SFX and /Music, synced to
   runtime/library/). Each folder needs a manifest.json; an asset is used ONLY if its manifest
   entry names a `source` and a `license` (e.g. Earshot SFX by Paul Urmson with its License.txt,
   or music beds you generated with ACE-Step 1.5, MIT). See .claude/skills/music-bed/.
2. Procedural fallbacks generated here from maths (no samples, no third-party rights): whoosh,
   pop, tick, chime, riser, and a soft original chord/arpeggio music bed seeded per video.

Mix: voice + music (ducked under speech with a sidechain compressor) + SFX, then EBU R128
loudness normalisation to -14 LUFS / -1.5 dBTP. Every asset used is written to attributions.
"""
from __future__ import annotations

import json
import random
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SR = 48000
SFX_CATEGORIES = ("whoosh", "pop", "tick", "chime", "riser", "impact")


# ------------------------------------------------------------------ io helpers
def write_wav(path: Path, x: np.ndarray) -> Path:
    x = np.clip(x, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def read_audio(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1",
                          "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0


# ------------------------------------------------------------------ procedural SFX
def _env(n, attack, release):
    e = np.ones(n, np.float32)
    a, r = int(attack * SR), int(release * SR)
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] *= np.linspace(1, 0, r) ** 2
    return e


def _lowpass(x, alpha):
    """One-pole low-pass with per-sample alpha (array) — cheap filter sweep."""
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += alpha[i] * (x[i] - acc)
        y[i] = acc
    return y


def synth_sfx(kind: str, seed: int = 0) -> np.ndarray:
    """Procedural effect, peak-limited to 0.9 so mixing never clips."""
    x = _synth_sfx(kind, seed).astype(np.float32)
    peak = float(np.abs(x).max()) if len(x) else 0.0
    return x * (0.9 / peak) if peak > 0.9 else x


def _synth_sfx(kind: str, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if kind == "whoosh":
        n = int(0.45 * SR)
        sweep = np.concatenate([np.linspace(0.02, 0.35, n // 2), np.linspace(0.35, 0.02, n - n // 2)])
        x = _lowpass(rng.standard_normal(n).astype(np.float32), sweep.astype(np.float32))
        return 0.9 * x / (np.abs(x).max() + 1e-9) * _env(n, 0.15, 0.25)
    if kind == "pop":
        n = int(0.12 * SR)
        t = np.arange(n) / SR
        f = np.linspace(700, 180, n)
        return (0.8 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 35)).astype(np.float32)
    if kind == "tick":
        n = int(0.03 * SR)
        t = np.arange(n) / SR
        return (0.6 * np.sin(2 * np.pi * 2400 * t) * np.exp(-t * 180)).astype(np.float32)
    if kind == "chime":
        n = int(1.1 * SR)
        t = np.arange(n) / SR
        x = sum(a * np.sin(2 * np.pi * f * t) for f, a in ((880, .5), (1318.5, .3), (1760, .2)))
        return (0.6 * x * np.exp(-t * 4)).astype(np.float32)
    if kind == "riser":
        n = int(1.0 * SR)
        t = np.arange(n) / SR
        f = np.linspace(180, 900, n)
        x = 0.5 * np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.25 * rng.standard_normal(n)
        return (0.5 * x * np.linspace(0, 1, n) ** 2 * _env(n, 0, 0.05)).astype(np.float32)
    if kind == "impact":
        n = int(0.5 * SR)
        t = np.arange(n) / SR
        x = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 9) + 0.3 * rng.standard_normal(n) * np.exp(-t * 40)
        return (0.8 * x).astype(np.float32)
    raise ValueError(kind)


# ------------------------------------------------------------------ procedural music
NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9}
PROGRESSIONS = [[0, 7, 9, 5], [9, 5, 0, 7], [0, 5, 9, 7]]   # I–V–vi–IV family (in semitones)


def _midi(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def synth_music(duration: float, seed: int, theme: str | None = None) -> np.ndarray:
    """Original ambient bed: soft pad chords + gentle arpeggio + light pulse; tempo follows the theme."""
    rnd = random.Random(seed)
    lo, hi = THEME_MOOD.get(theme or "", {}).get("bpm", (88, 100))
    bpm = rnd.choice([b for b in (84, 88, 92, 96, 100, 108, 116, 124) if lo <= b <= hi] or [92])
    root = 48 + NOTE[rnd.choice(list(NOTE))]
    prog = rnd.choice(PROGRESSIONS)
    n = int(duration * SR)
    t = np.arange(n) / SR
    out = np.zeros(n, np.float32)
    bar = 4 * 60 / bpm
    beat = 60 / bpm
    for b in range(int(duration / bar) + 1):
        start = int(b * bar * SR)
        if start >= n:
            break
        seg = slice(start, min(n, int((b + 1) * bar * SR) + int(0.5 * SR)))
        tt = t[seg] - b * bar
        deg = prog[b % len(prog)]
        minor = deg in (9, 4)
        chord = [root + deg, root + deg + (3 if minor else 4), root + deg + 7, root + deg + 12]
        env = np.minimum(1, tt / 0.8) * np.exp(-np.maximum(0, tt - bar) * 4)
        pad = sum(np.sin(2 * np.pi * _midi(m) * tt) + 0.3 * np.sin(4 * np.pi * _midi(m) * tt) for m in chord)
        out[seg] += 0.06 * pad * env
        for k in range(8):                                      # eighth-note arpeggio
            s = int((b * bar + k * beat / 2) * SR)
            if s >= n:
                break
            m = chord[[0, 1, 2, 3, 2, 1, 2, 3][k]] + 12
            L = min(int(0.4 * SR), n - s)
            ta = np.arange(L) / SR
            out[s:s + L] += 0.05 * np.sin(2 * np.pi * _midi(m) * ta) * np.exp(-ta * 9)
        for k in range(4):                                      # soft pulse on each beat
            s = int((b * bar + k * beat) * SR)
            if s >= n:
                break
            L = min(int(0.2 * SR), n - s)
            ta = np.arange(L) / SR
            out[s:s + L] += 0.07 * np.sin(2 * np.pi * 58 * ta) * np.exp(-ta * 22)
    fade = min(int(1.5 * SR), n)
    out[-fade:] *= np.linspace(1, 0, fade)
    out[: int(0.5 * SR)] *= np.linspace(0, 1, min(int(0.5 * SR), n))
    return out / (np.abs(out).max() + 1e-9) * 0.8


# ------------------------------------------------------------------ licensed asset libraries
@dataclass
class Asset:
    path: Path
    category: str
    source: str
    license: str


@dataclass
class Library:
    assets: list[Asset] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, folder: Path) -> "Library":
        lib = cls()
        mf = Path(folder) / "manifest.json"
        if not mf.exists():
            return lib
        for e in json.loads(mf.read_text()).get("assets", []):
            p = Path(folder) / e.get("file", "")
            if not (e.get("source") and e.get("license")):
                lib.rejected.append(f"{e.get('file')}: missing source/license")
            elif not p.exists():
                lib.rejected.append(f"{e.get('file')}: file missing")
            else:
                lib.assets.append(Asset(p, e.get("category", ""), e["source"], e["license"]))
        return lib

    def pick(self, category: str, rnd: random.Random) -> Asset | None:
        c = [a for a in self.assets if a.category == category]
        return rnd.choice(c) if c else None


# ------------------------------------------------------------------ cue planning + mix
def plan_cues(scenes: list[dict], timings: list[dict]) -> list[tuple[float, str]]:
    cues = [(0.0, "impact")]
    for i, (s, tm) in enumerate(zip(scenes, timings)):
        kind = s.get("visual", {}).get("type")
        if i > 0 and s.get("transition", "fade") != "cut":
            cues.append((max(0.0, tm["start"] - 0.2), "whoosh"))
        if kind == "stat":
            cues.append((tm["start"] + 0.45, "pop"))
        elif kind in ("bullets", "diagram", "timeline"):
            cues.append((tm["start"] + 0.5, "tick"))
        elif kind in ("question", "subscribe"):
            cues.append((tm["start"] + 0.3, "chime"))
    return sorted(cues)


def build_sfx_track(cues, duration: float, lib: Library, seed: int, attributions: list) -> np.ndarray:
    rnd = random.Random(seed)
    track = np.zeros(int(duration * SR) + SR, np.float32)
    cache: dict[str, np.ndarray] = {}
    for at, kind in cues:
        asset = lib.pick(kind, rnd)
        key = str(asset.path) if asset else f"synth:{kind}"
        if key not in cache:
            if asset:
                clip = read_audio(asset.path)[: int(2.5 * SR)]
                clip = clip / (np.abs(clip).max() + 1e-9) * 0.8
                attributions.append({"type": "sfx", "file": asset.path.name, "source": asset.source,
                                     "license": asset.license})
            else:
                clip = synth_sfx(kind, seed)
            cache[key] = clip
        clip = cache[key]
        s = int(at * SR)
        e = min(len(track), s + len(clip))
        track[s:e] += clip[: e - s]
    return track[: int(duration * SR)]


# ------------------------------------------------------------------ music analysis (numpy only)
# Target mood per daily theme: tempo range (BPM), energy 0–1 (loudness), brightness 0–1 (timbre).
THEME_MOOD = {
    "trending_search": {"bpm": (108, 132), "energy": 0.7, "bright": 0.6},   # upbeat, modern
    "trending_news": {"bpm": (100, 124), "energy": 0.6, "bright": 0.5},     # driving, newsy
    "tech_story": {"bpm": (84, 104), "energy": 0.4, "bright": 0.35},        # cinematic, curious
    "motivation": {"bpm": (88, 116), "energy": 0.55, "bright": 0.55},       # warm, uplifting
}
_ASR = 11025


def analyze_music(path: Path, seconds: float = 45.0) -> dict:
    """Tempo (onset autocorrelation), energy (RMS) and brightness (spectral centroid) of a track.
    Reads at most `seconds` at 11 kHz mono, so it takes well under a second per track."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-t", str(seconds), "-i", str(path), "-f", "s16le",
                          "-ac", "1", "-ar", str(_ASR), "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    if len(x) < _ASR * 4:
        return {"bpm": 0.0, "energy": 0.0, "bright": 0.0}
    hop, win = 256, 1024
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop] * np.hanning(win)
    spec = np.abs(np.fft.rfft(frames, axis=1))
    flux = np.maximum(np.diff(np.log1p(spec), axis=0), 0).sum(axis=1)
    flux -= flux.mean()
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    fps = _ASR / hop
    lags = np.arange(len(ac))
    ok = (lags >= fps * 60 / 180) & (lags <= fps * 60 / 60)          # 60–180 BPM
    bpm = 60 * fps / lags[ok][np.argmax(ac[ok])] if ok.any() else 0.0
    rms = float(np.sqrt(np.mean(x ** 2)))
    freqs = np.fft.rfftfreq(win, 1 / _ASR)
    centroid = float((spec * freqs).sum() / max(spec.sum(), 1e-9))
    return {"bpm": round(float(bpm), 1), "energy": round(min(rms / 0.25, 1.0), 3),
            "bright": round(min(centroid / 3000.0, 1.0), 3)}


def mood_distance(feat: dict, theme: str | None) -> float:
    m = THEME_MOOD.get(theme or "")
    if not m or not feat.get("bpm"):
        return 1.0
    lo, hi = m["bpm"]
    b = feat["bpm"]
    if any(lo <= c <= hi for c in (b, b * 2, b / 2)):      # half/double time counts as a match
        tempo = 0.0
    else:
        tempo = min(abs(b - lo), abs(b - hi)) / 40.0
    return tempo + abs(feat["energy"] - m["energy"]) + abs(feat["bright"] - m["bright"])


def pick_music(lib: Library, theme: str | None, rnd: random.Random) -> Asset | None:
    """Best mood match for the theme among licensed library tracks (features cached next to them)."""
    tracks = [a for a in lib.assets if a.category == "music"]
    if not tracks or not theme:
        return rnd.choice(tracks) if tracks else None
    cache_p = tracks[0].path.parent / "music_features.json"
    try:
        cache = json.loads(cache_p.read_text())
    except (OSError, ValueError):
        cache = {}
    scored = []
    for a in tracks:
        key = f"{a.path.name}:{a.path.stat().st_size}"
        if key not in cache:
            try:
                cache[key] = analyze_music(a.path)
            except (subprocess.CalledProcessError, ValueError):
                cache[key] = {"bpm": 0.0, "energy": 0.0, "bright": 0.0}
        scored.append((mood_distance(cache[key], theme), rnd.random(), a))
    try:
        cache_p.write_text(json.dumps(cache))
    except OSError:
        pass
    best = sorted(scored, key=lambda s: (s[0], s[1]))[:2]      # vary between the two best fits
    return rnd.choice(best)[2]


def music_track(duration: float, lib: Library, seed: int, workdir: Path, attributions: list,
                theme: str | None = None) -> Path:
    out = workdir / "music.wav"
    asset = pick_music(lib, theme, random.Random(seed))
    if asset:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", str(asset.path),
                        "-t", f"{duration:.3f}", "-ac", "1", "-ar", str(SR),
                        "-af", f"afade=t=out:st={max(0, duration - 1.5):.3f}:d=1.5", str(out)], check=True)
        attributions.append({"type": "music", "file": asset.path.name, "source": asset.source,
                             "license": asset.license})
    else:
        write_wav(out, synth_music(duration, seed, theme))
        attributions.append({"type": "music", "file": "procedural", "source": "ytauto procedural synth",
                             "license": "original work generated by this project"})
    return out


def mix(voice: Path, out: Path, duration: float, scenes: list[dict], timings: list[dict],
        workdir: Path, seed: int, music: bool = True, sfx: bool = True,
        sfx_dir: Path | None = None, music_dir: Path | None = None, theme: str | None = None) -> list[dict]:
    """Writes the final normalised mono narration+music+SFX track. Returns attributions."""
    attributions: list[dict] = []
    inputs, chains, labels = ["-i", str(voice)], ["[0:a]asplit=2[v][vsc]"], ["[v]"]
    idx = 1
    if music:
        m = music_track(duration, Library.load(music_dir) if music_dir else Library(), seed, workdir,
                        attributions, theme)
        inputs += ["-i", str(m)]
        chains.append(f"[{idx}:a]volume=0.22[m];[m][vsc]sidechaincompress=threshold=0.015:ratio=12:"
                      f"attack=20:release=400:makeup=1[md]")
        labels.append("[md]")
        idx += 1
    else:
        chains[0] = "[0:a]anull[v]"
    if sfx:
        track = build_sfx_track(plan_cues(scenes, timings), duration,
                                Library.load(sfx_dir) if sfx_dir else Library(), seed, attributions)
        sp = write_wav(workdir / "sfx.wav", track)
        inputs += ["-i", str(sp)]
        chains.append(f"[{idx}:a]volume=0.35[s]")
        labels.append("[s]")
    graph = ";".join(chains) + ";" + "".join(labels) + \
        f"amix=inputs={len(labels)}:normalize=0:duration=first[out]"
    premix = workdir / "premix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
                    "-ar", str(SR), "-ac", "1", str(premix)], check=True)
    from .audio import normalize_two_pass
    normalize_two_pass(premix, out, -14.0, -1.5)          # accurate -14 LUFS (single pass undershot)
    premix.unlink(missing_ok=True)
    return attributions
