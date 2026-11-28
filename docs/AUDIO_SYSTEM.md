# Audio system

Config: `config/audio.yaml`, `config/pronunciation.yaml` (+ Pronunciation tab).

1. **Voice**: Google Chirp 3 HD (within free-tier caps) → Kokoro (offline) → Piper; one voice per video; clips
   cached by exact text. The pronunciation dictionary and acronym rule change only the spoken text, never captions.
2. **Cue sheet** (`audio/cues.py`): every SFX/music cue has a reason tied to the picture; no whoosh on plain fades.
3. **Mix** (`audio/stems.py`): dialogue-anchored gains — dialogue −20 LUFS, music −14 dB and ambience −24 dB
   relative; HPF, de-esser, compressor, a music speech-band dip and sidechain ducking (threshold 0.03, ratio 4).
   Four stereo 48 kHz stems are exported: dialogue, music, sfx, ambience.
4. **Master** (`audio/mastering.py`): −14 LUFS integrated, −1 dBTP.
5. **QC** (`audio/qc.py`, blocks submit): clipping, true peak, LUFS, LRA, silence, speech-over-music ratio,
   empty or corrupt audio, and ASR word error rate (`audio/speech.py`, faster-whisper base.en on CPU) with
   number/unit/acronym normalisation. Tamil ASR is reported as unknown, never as passed.
