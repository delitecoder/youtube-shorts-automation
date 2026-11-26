# Style reference (director + camera man)

The reference-video measurements (`python -m ytauto analyze-reference` → runtime/reference/analysis.json:
cuts/min, average shot length, words per minute, hook in the first 3 s, contact sheets) will be summarised
here once reference videos are in Drive `01_Inbox/References/Style`. Reference videos are only
analysed, never reused.

## Canvas and safe zones (1080×1920)
- Visual area: left 90, top 440, 840×740. `full_frame`: left 0, top 200, 1080×1040.
- Keep the bottom 680 px (captions, title) and the right 150 px (YouTube like/share buttons) clear.
- Labels, tags and numbers never overlap each other; leave ≥ 24 px between text blocks.

## Motion source of truth
HyperFrames' measured guides ship in every container:
`renderer/hyperframes/node_modules/hyperframes/dist/skills/hyperframes/references/` —
`motion.md` (motion craft, transitions with a cause), `look.md`, `captions.md`, `film.md`.
The built-in templates follow them; the camera man reads motion.md before coding.
Per video the renderer rotates a palette, a set of 3–5 transition kinds (blur / whip / iris /
colour flood / cut), a title entrance (per-word blur, grey→ink, line blur) and a caption style
(highlight / pill / underline), so consecutive days never look or move the same.
A storyboard may pin them: `"palette": "blueprint"`, `"style": {"transitions": [...], "title": "...", "captions": "..."}`.

## Stickman (main look for Shorts since 2026-11-26)
Code-drawn stick figure (`production/stickman.py`, visual type `stickman`), after Stickman Video
Director's B1 Minimal Line Art style (MIT, github.com/kaomei/stickman-video-director; no code or
assets copied, no image generation): slender ink figure with a hand-drawn line boil, big Caveat
handwriting, coloured props, keyed poses with anticipation, head leading, held reactions.
Looks alternate by day: warm paper (ink on #FDFDFC, faint grid) / chalkboard (white ink on the
day's dark palette). Director review: the face must stay visible, the text must not touch the
figure or prop, every pose change must land on the line that causes it.

## 3D (three.js)
`renderer/hyperframes/three_scenes.js` (three.js 0.170, MIT; WebGL in headless Chromium, no GPU
needed): templates servers / globe / stack / bars, drawn purely from timeline time, warm key +
cool rim light, contact shadow, one camera dive, labels that follow objects and never overlap.
About 5 s of render per second of 3D on this server; max 3 per Short, 8 per long video.
Open-source image models (e.g. Qwen-Image, 20B) were evaluated and rejected: they need a large
GPU; on this CPU-only server one image would take hours.

## No AI slop
- Script: no stock phrases (validation rejects them), concrete names/numbers/stakes only.
- Motion: no pulsing/breathing/floating loops, no creeping zooms, no bounce on UI, no identical
  entrances, no decorative particles/glow blobs. Motion explains the idea or it is removed.
- Images: real licensed photos/screenshots only; never AI-generated images.

## Reference videos
`python -m ytauto analyze-reference` (daily; only new files in Drive References/Style are
downloaded and analysed — results are kept in the control sheet). The director looks at the
contact sheet and records 1–3 concrete learnings with `python -m ytauto style-note --add "…"`;
`discover` shows the notes to every later run.

## Motion language (system design / tech explainers)
- One idea per beat: each narration phrase gets one visual change (grow, connect, highlight, count).
- Build diagrams step by step (box → arrow → box), flow dots along arrows for requests or data.
- Numbers count up; comparisons grow side by side; the key result gets the accent colour (no pulse).
- Hold the final state ≥ 1 s before the scene ends. No randomness, timers or network.
- Vary layout and colour accents between videos (don't reuse the last video's scene designs).

## Director review checklist (`python -m ytauto frames`)
1. Is anything clipped, overlapping or inside the safe zones?
2. Is every text readable at phone size (≥ 40 px for labels, ≥ 64 px for key numbers)?
3. Does the visual change when the narration says it (timing)?
4. Any `fallback` in custom_scenes → fix the code or re-brief once. At most 2 rounds.
