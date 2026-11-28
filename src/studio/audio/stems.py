"""Multi-stem production mix: dialogue · music · sfx · ambience → stereo master (config/audio.yaml).

Each stem is processed separately and exported (stems/*.wav, 48 kHz stereo) next to the cue sheet:
- dialogue: high-pass, de-esser, gentle compression;
- music: base gain, a dip in the 1-4 kHz speech band, sidechain-ducked under the dialogue;
- sfx: per-cue gain and stereo pan from the cue sheet;
- ambience: low bed, lightly ducked.
The sum is two-pass loudness normalised with a true-peak ceiling; measured values are returned.
"""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

import numpy as np

from studio.audio import cues as cuesmod
from studio.audio.mix import SR, Library, read_audio, synth_sfx, music_track
from studio.core import conf

FALLBACK_SOUND = {"fail": "impact", "success": "chime", "footstep": "tick", "typing": "tick", "notify": "chime",
                  "door": "impact", "money": "chime", "machine": "tick"}


def _db(x: float) -> float:
    return 10 ** (x / 20)


def render_cue_stems(cue_list, duration: float, lib: Library, seed: int, attributions: list) -> tuple[np.ndarray, np.ndarray]:
    """Returns (sfx_stereo, ambience_stereo) float32 arrays [n, 2] built from the cue sheet."""
    rnd = random.Random(seed)
    n = int(duration * SR)
    sfx, amb = np.zeros((n, 2), np.float32), np.zeros((n, 2), np.float32)
    cache: dict[str, np.ndarray] = {}
    for c in cue_list:
        is_amb = c.stem == "ambience"
        asset = lib.pick(c.sound, rnd)
        if is_amb and not asset:
            continue                                   # never fake an ambience with synth noise
        key = str(asset.path) if asset else f"synth:{c.sound}"
        if key not in cache:
            if asset:
                clip = read_audio(asset.path)[: int((8.0 if is_amb else 2.5) * SR)]
                clip = clip / (np.abs(clip).max() + 1e-9)
                attributions.append({"type": "sfx", "file": asset.path.name, "source": asset.source,
                                     "license": asset.license, "attribution_required": asset.attribution_required})
            else:
                kind = c.sound if c.sound in ("whoosh", "pop", "tick", "chime", "impact") else FALLBACK_SOUND.get(c.sound, "pop")
                clip = synth_sfx(kind, seed)
                clip = clip / (np.abs(clip).max() + 1e-9)
            cache[key] = clip.astype(np.float32)
        clip = cache[key] * _db(c.gain_db)
        f = max(1, int(c.fade_s * SR))
        clip = clip.copy()
        clip[:f] *= np.linspace(0, 1, min(f, len(clip)))
        left, right = np.sqrt((1 - c.pan) / 2), np.sqrt((1 + c.pan) / 2)          # constant-power pan
        s = int(c.t * SR)
        e = min(n, s + len(clip))
        if e <= s:
            continue
        tgt = amb if is_amb else sfx
        tgt[s:e, 0] += clip[: e - s] * left
        tgt[s:e, 1] += clip[: e - s] * right
    return sfx, amb


def _lufs(path: Path) -> float | None:
    from studio.audio.qc import loudness
    v = loudness(path)["integrated_lufs"]
    return None if v is None or v <= -69 else v          # ebur128 reports digital silence as -70


def _gain_to(path: Path, target: float, fallback_db: float = 0.0) -> float:
    """dB gain that brings `path` to `target` LUFS (measured, so every source/engine is treated alike)."""
    cur = _lufs(path)
    return fallback_db if cur is None else max(-40.0, min(40.0, target - cur))


def _stereo_wav(path: Path, x: np.ndarray) -> Path:
    import soundfile as sf
    sf.write(str(path), np.clip(x, -1, 1).astype(np.float32), SR, subtype="PCM_16")
    return path


def mix(voice: Path, out: Path, duration: float, scenes: list[dict], timings: list[dict], workdir: Path, seed: int,
        music: bool = True, sfx: bool = True, sfx_dir: Path | None = None, music_dir: Path | None = None,
        theme: str | None = None, mood: str | None = None, cfg: dict | None = None) -> list[dict]:
    """Same contract as the old mono mix (returns attributions) plus stems/, cue_sheet.json, audio_mix.json."""
    c = cfg or conf.load("audio")
    stems = workdir / "stems"
    stems.mkdir(parents=True, exist_ok=True)
    attributions: list[dict] = []
    cue_list = cuesmod.plan(scenes, timings) if sfx else []
    problems = cuesmod.check(cue_list, duration)
    (workdir / "cue_sheet.json").write_text(json.dumps({"cues": [x.as_dict() for x in cue_list], "problems": problems},
                                                       indent=1))
    sfx_st, amb_st = render_cue_stems(cue_list, duration, Library.load(sfx_dir) if sfx_dir else Library(), seed,
                                      attributions)
    _stereo_wav(stems / "sfx.wav", sfx_st)
    _stereo_wav(stems / "ambience.wav", amb_st)
    music_src = None
    if music:
        music_src = music_track(duration, Library.load(music_dir) if music_dir else Library(), seed, workdir,
                                attributions, theme, mood)
    d, m = c["dialogue"], c["music"]
    comp = d["compressor"]
    duck = m["duck"]
    dlg_gain = _gain_to(voice, d["target_lufs"])
    mus_gain = _gain_to(music_src, d["target_lufs"] + m["relative_to_dialogue_db"], m["base_gain_db"]) if music_src else 0.0
    amb_gain = _gain_to(stems / "ambience.wav", d["target_lufs"] + c["ambience"]["relative_to_dialogue_db"])
    chains = [f"[0:a]aresample={SR},volume={dlg_gain:.2f}dB,pan=stereo|c0=c0|c1=c0,highpass=f={d['highpass_hz']}"
              + (",deesser" if d.get("deess") else "")
              + f",acompressor=threshold={comp['threshold_db']}dB:ratio={comp['ratio']}:attack={comp['attack_ms']}"
              f":release={comp['release_ms']},asplit=3[dlg][key][key2]"]
    inputs = ["-i", str(voice)]
    labels = ["[dlg]"]
    idx = 1
    if music_src:
        inputs += ["-i", str(music_src)]
        chains.append(f"[{idx}:a]aresample={SR},pan=stereo|c0=c0|c1=c0,volume={mus_gain:.2f}dB,"
                      f"equalizer=f=2500:t=h:w=3000:g={m['speech_band_notch_db']}[mus];"
                      f"[mus][key]sidechaincompress=threshold={duck['threshold']}:ratio={duck['ratio']}:"
                      f"attack={duck['attack_ms']}:release={duck['release_ms']}[musd]")
        labels.append("[musd]")
        idx += 1
    else:
        chains.append("[key]anullsink")
    inputs += ["-i", str(stems / "sfx.wav"), "-i", str(stems / "ambience.wav")]
    chains.append(f"[{idx}:a]aresample={SR}[fx]")
    chains.append(f"[{idx + 1}:a]aresample={SR},volume={amb_gain:.2f}dB[amb0];[amb0][key2]sidechaincompress=threshold=0.05:ratio=3[amb]")
    labels += ["[fx]", "[amb]"]
    graph = ";".join(chains) + ";" + "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=first[mix]"
    premix = workdir / "premix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[mix]",
                    "-ar", str(SR), "-ac", "2", "-t", f"{duration:.3f}", str(premix)], check=True)
    # processed dialogue and music stems for export (same chains, no summing)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(voice), "-af",
                    f"aresample={SR},volume={dlg_gain:.2f}dB,pan=stereo|c0=c0|c1=c0,highpass=f={d['highpass_hz']}"
                    + (",deesser" if d.get("deess") else "")
                    + f",acompressor=threshold={comp['threshold_db']}dB:ratio={comp['ratio']}",
                    "-t", f"{duration:.3f}", str(stems / "dialogue.wav")], check=True)
    if music_src:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(music_src), "-i", str(stems / "dialogue.wav"),
                        "-filter_complex", f"[0:a]aresample={SR},pan=stereo|c0=c0|c1=c0,volume={mus_gain:.2f}dB,"
                        f"equalizer=f=2500:t=h:w=3000:g={m['speech_band_notch_db']}[mus];[mus][1:a]sidechaincompress="
                        f"threshold={duck['threshold']}:ratio={duck['ratio']}:attack={duck['attack_ms']}:release={duck['release_ms']}[o]",
                        "-map", "[o]", "-t", f"{duration:.3f}", str(stems / "music.wav")], check=True)
    else:
        _stereo_wav(stems / "music.wav", np.zeros((int(duration * SR), 2), np.float32))
    from studio.audio.mastering import normalize_two_pass
    ms = c["master"]
    report = normalize_two_pass(premix, out, ms["integrated_lufs"], ms["true_peak_dbtp"], channels=ms.get("channels", 2))
    premix.unlink(missing_ok=True)
    (workdir / "audio_mix.json").write_text(json.dumps({"target": ms, "measured": report, "cue_problems": problems,
                                                        "gains_db": {"dialogue": round(dlg_gain, 2), "music": round(mus_gain, 2),
                                                                     "ambience": round(amb_gain, 2)},
                                                        "stems": sorted(p.name for p in stems.glob("*.wav"))}, indent=1,
                                                       default=str))
    return attributions
