"""Speech verification (WER) and captions timed from actual speech."""
from pathlib import Path

import pytest

from studio.audio import speech
from studio.subtitles import captions


def test_wer_normalises_numbers_units_acronyms_and_asr_spellings():
    assert speech.wer("twelve milliseconds", "12ms") == 0.0
    assert speech.wer("A W S is fast", "AWS is fast") == 0.0
    assert speech.wer("jay-son payload", "Jason payload") == 0.0
    assert speech.wer("the cache is warm", "the cash is cold") > 0.2
    assert speech.wer("", "") == 0.0 and speech.wer("", "x") == 1.0


def test_align_maps_script_words_to_spoken_times():
    script = ["Hash", "tables", "find", "data", "fast."]
    asr = [("hash", 0.1, 0.3), ("tables", 0.3, 0.7), ("find", 0.8, 1.0), ("data", 1.0, 1.3), ("fast", 1.4, 1.8)]
    al = speech.align(script, asr)
    assert all(al) and al[4] == ("fast.", 1.4, 1.8)


def test_build_aligned_uses_speech_times_and_interpolates_gaps():
    scenes = [{"narration": "Hash tables find data fast."}]
    timings = [{"speech_start": 0.0, "speech_end": 2.0, "start": 0, "end": 2.2}]
    asr = [("hash", 0.1, 0.3), ("tables", 0.3, 0.7), ("data", 1.0, 1.3), ("fast", 1.4, 1.8)]   # 'find' missed
    caps, st = captions.build_aligned(scenes, timings, asr)
    words = [w for c in caps for w in c["words"]]
    assert words[0][1] == 0.1 and words[-1][2] == 1.8
    find = next(w for w in words if w[0] == "find")
    assert 0.7 <= find[1] <= find[2] <= 1.0                   # interpolated between its neighbours
    assert st["matched_ratio"] == 0.8 and captions.check(caps) == []


def test_build_aligned_falls_back_to_estimate_when_asr_timing_is_degenerate():
    # regression VIDEO-20261128-001: ASR stretched "a" to the end of the scene and dropped the last words,
    # so the interpolated tail ran past the next scene's start ("caption starts before the previous one").
    scenes = [{"narration": "about 822 million a day. Guessing does not scale."},
              {"narration": "In any payment system."}]
    timings = [{"speech_start": 0.0, "speech_end": 3.7, "start": 0, "end": 3.8},
               {"speech_start": 3.8, "speech_end": 5.5, "start": 3.8, "end": 5.6}]
    asr = [("about", 0.0, 0.2), ("822", 0.2, 0.9), ("million", 0.9, 1.3), ("a", 1.3, 3.8), ("day.", 3.8, 3.8),
           ("In", 3.8, 4.6), ("any", 4.6, 4.8), ("payment", 4.8, 5.1), ("system,", 5.1, 5.5)]
    caps, st = captions.build_aligned(scenes, timings, asr)
    assert captions.check(caps) == []
    first = [w for c in caps for w in c["words"]][:9]
    assert all(w[2] <= 3.7 + 1e-6 for w in first)
    assert st["estimated_scenes"] == [1]


def test_caption_check_catches_overlap_and_order():
    caps = [{"start": 0.0, "end": 2.0, "words": [("a", 0, 1), ("b", 1, 2)]},
            {"start": 1.0, "end": 3.0, "words": [("c", 1, 2), ("d", 2, 3)]}]
    assert any("overlap" in p for p in captions.check(caps))


@pytest.mark.slow
def test_real_tts_is_verified_by_asr(tmp_path):
    from studio.audio.tts import KokoroTTS
    models = Path(__file__).resolve().parents[1] / "runtime" / "models" / "kokoro-v1.0.int8.onnx"
    if not models.exists() or not speech.available("en"):
        pytest.skip("Kokoro models or faster-whisper not available")
    text = "The index lives in memory, so lookups take twelve milliseconds."
    out = KokoroTTS(voice="am_michael").synthesize(text, tmp_path / "k.wav")
    words = speech.transcribe(out, "en")
    assert speech.wer(text, " ".join(w[0] for w in words)) <= 0.15
    assert words and words[0][1] < 1.0
