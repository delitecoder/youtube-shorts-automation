---
paths: ["src/studio/audio/**", "src/studio/video/**", "config/audio.yaml", "config/video.yaml"]
---
- QC thresholds live in config YAML, not code. Never lower a threshold to make a render pass; fix the cause.
- Master −14 LUFS / −1 dBTP; dialogue must stay clearly above music (audio QC speech/music check).
- Run `python -m pytest -q tests/test_audio_video_qc.py tests/test_speech_captions.py` after changes.
