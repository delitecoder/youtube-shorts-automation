# Video system

Config: `config/video.yaml`, `config/styles.yaml`. No AI video-generation models are used.

- **Style**: `studio plan` picks a style from the registry (experiment arm first, otherwise topic fit and not
  used in the last 4 videos) and writes `style_id@v`, palette and style into the storyboard.
- **Originality**: content memory blocks an identical hook, script similarity ≥ 0.6, ≥ 3 identical scene
  signatures in a row, or animation code similarity ≥ 0.6. Tamil versions are exempt.
- **Render**: HyperFrames (HTML/GSAP) with a Pillow fallback for Shorts. A render manifest hashes every input;
  the output is reused only if the hash matches and the earlier render passed QC.
- **Captions**: aligned to the measured speech (ASR word timings) when available.
- **QC** (`video/qc.py`, merged by `render.full_qc`, blocks submit): decodable container, black frames, frozen
  frames, resolution/aspect, A/V duration drift.
- **Review**: `scene-frames` (per-scene stills) and `frames` (review sheet) for the director.
