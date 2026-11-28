"""Audio production (stems, cues, pronunciation, mastering) and objective audio/video QC."""
import json
import subprocess

import numpy as np
import pytest
import soundfile as sf

from studio.audio import cues as cuesmod
from studio.audio import pronunciation as pron
from studio.audio import qc as aqc
from studio.audio import stems
from studio.video import qc as vqc

SR = 48000


def tone(path, seconds, freq=220.0, amp=0.3, gaps=()):
    t = np.arange(int(seconds * SR)) / SR
    x = amp * np.sin(2 * np.pi * freq * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))   # speech-like envelope
    for a, b in gaps:
        x[int(a * SR):int(b * SR)] = 0
    sf.write(str(path), x.astype(np.float32), SR)
    return path


SCENES = [{"visual": {"type": "title"}, "transition": "cut"},
          {"visual": {"type": "stat"}, "transition": "fade"},
          {"visual": {"type": "diagram"}, "transition": "swipe",
           "audio_events": [{"at": 0.5, "event": "server_start", "sound": "impact", "reason": "server rack powers on"}]},
          {"visual": {"type": "story", "location": "street",
                      "cast": [{"id": "arjun", "walk_from": "left", "say": "hi"}]}, "transition": "fade"}]
TIMINGS = [{"start": 0, "end": 2}, {"start": 2, "end": 4}, {"start": 4, "end": 6}, {"start": 6, "end": 8}]


# ---------------- cue sheet
def test_every_cue_has_a_reason_and_plain_fades_get_no_whoosh():
    cs = cuesmod.plan(SCENES, TIMINGS)
    assert all(c.reason for c in cs)
    whooshes = [c for c in cs if c.sound == "whoosh"]
    assert len(whooshes) == 1 and whooshes[0].scene == 2               # only the swipe moves the frame
    assert not any(c.t == 0 and c.sound == "impact" for c in cs)        # no reasonless opening impact
    assert any(c.event == "server_start" for c in cs)
    assert any(c.stem == "ambience" for c in cs) and any(c.event == "footstep" for c in cs)
    assert cuesmod.check(cs, 8.0) == []


def test_declared_audio_event_without_reason_is_rejected():
    with pytest.raises(ValueError, match="no reason"):
        cuesmod.plan([{"visual": {"type": "title"}, "audio_events": [{"at": 0.1, "event": "x"}]}], [{"start": 0, "end": 1}])


# ---------------- pronunciation
def test_pronunciation_changes_spoken_form_only():
    table, words = pron.load(None, "en")
    text = "Store JSON in PostgreSQL behind nginx on AWS with 16 GB RAM and an LLM."
    spoken = pron.apply(text, table, words)
    assert "jay-son" in spoken and "post-gres Q L" in spoken and "engine x" in spoken
    assert "A W S" in spoken and "RAM" in spoken and "L L M" in spoken
    assert "sequel" not in spoken                       # PostgreSQL handled before the shorter SQL entry
    rules = {a["term"]: a["rule"] for a in pron.audit(text, table, words)}
    assert rules["JSON"] == "dictionary" and rules["AWS"] == "spelled_acronym" and rules["RAM"] == "word_acronym"


def test_sheet_overrides_and_language_specific_entries():
    class S:
        def rows(self, tab):
            return [{"term": "JSON", "spoken": "J son", "lang": "ta", "status": "verified"}]
    en, _ = pron.load(S(), "en")
    ta, _ = pron.load(S(), "ta")
    assert en["JSON"] == "jay-son" and ta["JSON"] == "J son"


# ---------------- stem mix + mastering + audio QC
@pytest.fixture
def mixed(tmp_path):
    voice = tone(tmp_path / "voice.wav", 8.0, gaps=[(1.9, 2.1), (5.9, 6.1)])
    out = tmp_path / "narration.wav"
    stems.mix(voice, out, 8.0, SCENES, TIMINGS, tmp_path, seed=3)
    return tmp_path, out


def test_stem_mix_exports_four_stereo_stems_and_cue_sheet(mixed):
    wd, out = mixed
    for s in ("dialogue", "music", "sfx", "ambience"):
        x, sr = sf.read(str(wd / "stems" / f"{s}.wav"), always_2d=True)
        assert sr == SR and x.shape[1] == 2, s
    sheet = json.loads((wd / "cue_sheet.json").read_text())
    assert sheet["cues"] and all(c["reason"] for c in sheet["cues"])
    assert sf.info(str(out)).channels == 2


def test_audio_qc_passes_a_clean_mix(mixed):
    wd, out = mixed
    r = aqc.check(out, wd / "stems", video_duration=8.0)
    assert r["pass"], r["critical"]
    assert abs(r["measured"]["integrated_lufs"] + 14) <= 1.5
    assert r["measured"]["speech_to_music_db"] >= 10


def test_audio_qc_blocks_clipping_silence_and_masking(tmp_path):
    clip = tmp_path / "clip.wav"
    sf.write(str(clip), np.clip(np.sin(np.arange(SR * 3) / 5) * 2.0, -1, 1).astype(np.float32), SR)
    r = aqc.check(clip)
    assert not r["pass"] and any("clipped" in c for c in r["critical"])
    silent = tmp_path / "silent.wav"
    sf.write(str(silent), np.zeros(SR * 3, np.float32), SR)
    assert any("silent" in c for c in aqc.check(silent)["critical"])
    missing = aqc.check(tmp_path / "nope.wav")
    assert not missing["pass"] and "missing" in missing["critical"][0]
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    tone(stems_dir / "dialogue.wav", 4, amp=0.05)
    tone(stems_dir / "music.wav", 4, freq=330, amp=0.3)              # music louder than speech
    for s in ("sfx", "ambience"):
        sf.write(str(stems_dir / f"{s}.wav"), np.zeros((SR * 4, 2), np.float32), SR)
    final = tone(tmp_path / "final.wav", 4, amp=0.2)
    r = aqc.check(final, stems_dir)
    assert any("masks speech" in c for c in r["critical"])


def test_audio_qc_flags_asr_mismatch(mixed):
    wd, out = mixed
    assert any("WER" in c for c in aqc.check(out, wd / "stems", wer=0.4)["critical"])


def test_silence_detector_ignores_edges_and_finds_internal_gaps():
    x = np.zeros((SR * 6, 1), np.float32)
    x[SR:2 * SR] = 0.3
    x[4 * SR:5 * SR] = 0.3
    gaps = aqc.silences(x, SR, min_s=1.2)
    assert len(gaps) == 1 and 1.9 <= gaps[0][0] <= 2.1


# ---------------- video QC
def _video(path, color="blue", seconds=2, size="1080x1920", audio=True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}"]
    if color == "black":
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:size={size}:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:duration={seconds}", "-shortest"]
    subprocess.run(cmd + ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)
    return path


def test_video_qc_passes_good_video(tmp_path):
    r = vqc.check(_video(tmp_path / "ok.mp4"))
    assert r["pass"], r["critical"]
    assert r["measured"]["decode_ok"] and r["measured"]["black_ratio"] < 0.02


def test_video_qc_blocks_black_wrong_size_missing_audio_and_corrupt(tmp_path):
    assert any("black" in c for c in vqc.check(_video(tmp_path / "b.mp4", "black"))["critical"])
    assert any("resolution" in c for c in vqc.check(_video(tmp_path / "s.mp4", size="640x360"))["critical"])
    assert any("no audio" in c for c in vqc.check(_video(tmp_path / "n.mp4", audio=False))["critical"])
    bad = tmp_path / "bad.mp4"
    good = (_video(tmp_path / "g.mp4")).read_bytes()
    bad.write_bytes(good[: len(good) // 2])
    assert not vqc.check(bad)["pass"]
