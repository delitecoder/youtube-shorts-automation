# Style reference (director + camera man)

The reference-video measurements (`python -m ytauto analyze-reference` → runtime/reference/analysis.json:
cuts/min, average shot length, words per minute, hook in the first 3 s, contact sheets) will be summarised
here once reference videos are in Drive `01_Inbox/References/Style`. Reference videos are only
analysed, never reused.

## Canvas and safe zones (1080×1920)
- Visual area: left 90, top 440, 840×740. `full_frame`: left 0, top 200, 1080×1040.
- Keep the bottom 680 px (captions, title) and the right 150 px (YouTube like/share buttons) clear.
- Labels, tags and numbers never overlap each other; leave ≥ 24 px between text blocks.

## Motion language (system design / tech explainers)
- One idea per beat: each narration phrase gets one visual change (grow, connect, highlight, count).
- Build diagrams step by step (box → arrow → box), flow dots along arrows for requests or data.
- Numbers count up; comparisons grow side by side; the key result gets a pulse or glow, once.
- Hold the final state ≥ 1 s before the scene ends. No randomness, timers or network.
- Vary layout and colour accents between videos (don't reuse the last video's scene designs).

## Director review checklist (`python -m ytauto frames`)
1. Is anything clipped, overlapping or inside the safe zones?
2. Is every text readable at phone size (≥ 40 px for labels, ≥ 64 px for key numbers)?
3. Does the visual change when the narration says it (timing)?
4. Any `fallback` in custom_scenes → fix the code or re-brief once. At most 2 rounds.
