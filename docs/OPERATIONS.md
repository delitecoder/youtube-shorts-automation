# Operations — daily runbook

Command entry point: `PYTHONPATH=src python -m studio <command>` (`python -m ytauto` still works and forwards
here). Directive names are aliases: `research`→`record-research`, `produce`→`render`, `audio`→`voice`,
`publish`→`reconcile`; `qc`, `report`, `approve` (read-only status) and `playlist` are native.
Recovery after an interruption: see docs/RECOVERY.md. Model routing: docs/MODEL_ROUTING.md.

## Usage budget (check before working — finish inside the current window when possible)
How to read the real numbers (costs almost nothing):
1. `get_session` (no id) → `session_context`/`external_metadata.rate_limit_info` (status, resetsAt).
2. `list_events` for your own session id with `kinds: ["rate_limit_event"]`, `limit: 20` → take the
   newest event's `rate_limit_info.unifiedWindows`: `five_hour.utilization/resetsAt/status` and
   `seven_day.utilization/resetsAt/status`.
3. `python -m studio budget --five-hour U5 --five-reset R5 --seven-day U7 --seven-reset R7
   [--five-status S5 --seven-status S7] --record start` and follow `decision`:
   - `go_full`: normal workflow.
   - `go_lean`: lean plan — one research pass, ONE storywriter revision if the review fails
     (fix minor issues yourself instead of a revision; stop and checkpoint only if the revised
     version still fails), `VISION_PROVIDERS=clip,metadata` (or metadata only),
     skip analytics snapshot and Reddit drafts, keep subagent prompts short.
   - `wait`: arm `send_later` for `resume_after_minutes`, checkpoint, stop (start after the reset).
   - `weekly_stop`: stop now. The command already queued an alert email ("weekly Claude limit
     reached, no videos until <reset>"). One video per day is never exceeded.
   If `get_session` / `list_events` are not available in this session (scheduled runs usually lack
   them), run `python -m studio budget --unknown` instead: it returns `go_lean` without inventing
   numbers; skip `--record` and the `send_later` safety net in that case.
4. Re-check (steps 2–3 without `--record`) before research, before render, and before submit; if the
   decision got worse, switch to lean or wait at that point instead of running out mid-step.
5. After `submit` (or when stopping), run the same command with `--record end` so the per-video cost
   is learned (Configuration tab).
An Apps Script watchdog independently emails the owner if no video exists by 12:30 IST (covers the
case where Claude is fully blocked and cannot even start).

## Usage-limit safety net (dynamic resume)
Claude subscription usage resets about 5 hours after usage starts, so resumes follow the reset,
not a fixed clock:
- As soon as `discover` returns work (`resume` or a new item), call the `send_later` tool with
  `delay_minutes: 310` and the message "Usage window has reset: continue the YouTube Short run —
  run `python -m studio discover` and continue (resume) per CLAUDE.md". Keep the returned trigger id.
- If you are woken by that message, first arm a new 310-minute reminder the same way, then continue.
- When the run ends normally (video submitted, or nothing to do), delete your reminder with
  `delete_trigger`. Never leave more than one reminder armed.
- If `send_later` is not available, skip this; the next daily run resumes from the checkpoint.
- `submit` automatically picks the next free 19:30 IST slot, so a late video never collides with
  the next day's video.

## Daily workflow (scheduled task)
Run from the repo root with `PYTHONPATH=src`. Always install with `python -m pip` (plain `pip` can
belong to a different Python than `python` in fresh containers). Work files go in `runtime/work/<VIDEO_ID>/`.

1. `python -m studio doctor` → stop and report if `ok` is false.
   `bash scripts/fetch_kokoro.sh` (offline fallback voice), `bash scripts/install_fonts.sh`
   (Noto Sans Tamil for Tamil videos) and `python -m studio sync-library`
   (SFX/music libraries; procedural sound is used when empty; also caches the DeliteCoder logo
   badge from Drive `References/Images/brand_logo.png`, shown in every video).
   `python -m studio import-audio` (licensed SFX + music from Stickman Video Director's library,
   CC0/CC BY; cached in the container).
   `python -m studio analyze-reference` (only NEW reference videos from Drive References/Style are
   analysed; if `new` > 0, look at each `contact_sheet` and record 1–3 concrete learnings with
   `python -m studio style-note --add "…"`; discover shows all notes as `insights.style_notes`).
   `python -m studio apps-script-check` — if the owner's approval Apps Script is older than this
   repo, the approver is emailed once with what changed and how to update. Never push or edit the
   deployed Apps Script yourself.
2. `python -m studio reconcile` (marks scheduled videos public, moves files, optional promotion).
3. `python -m studio audit --email` (daily views audit: trends, top/bottom videos, traffic sources,
   retention → concrete suggestions; emails the owner once a day) → pass its `guidance` and
   `suggestions` to the storywriter, who must apply them. `python -m studio peak-time` (weekly,
   cached) keeps the per-weekday peak publish slots current; `submit` uses them automatically.
   `python -m studio analytics --snapshot` keeps per-video history for our own videos.
4. `python -m studio discover` →
   - `resume`: an earlier run stopped part-way (e.g. token limit). Run
     `python -m studio restore --video-id <id>` and continue from the reported `next_step`
     (research → storyboard → review → render → submit), skipping steps whose files exist.
   - `none`: report the reason and stop.
   - `existing_video`: `python -m studio prepare-existing --file-id <id>`. If not ok, stop (a
     manual-review notification is emailed). If ok: **storywriter** writes `metadata.json` only →
     **quality-reviewer** writes `review.json` → `python -m studio submit --video-id … --metadata … --review …`.
   - `document`: `python -m studio new-item --source-file-id <id>` →
     `python -m studio doc-text --file-id <id>` → continue with step 5 (source_type drive_document).
   - `research`: `python -m studio new-item` → step 5. The output's `theme` is today's content
     theme (Shorts: `trending_mix`: the most trending of tech news / trending search / quick system
     design / tech story, never the lane in `theme.recent_lanes`; never a repeated story). A row for today in the sheet tab `ThemePlan`
     (date, theme, details) overrides it (`theme.source`). Pass the whole `theme` AND `insights`
     to the researcher and storywriter.
   - ALWAYS plan from `insights` (discover runs today's analytics audit itself if missing): the
     exact search terms viewers used to find us, the videos YouTube suggests us next to, and the
     best/weakest videos per format. If `insights.available` is false (no data yet), say so in the
     summary and continue with the theme.
5. **researcher** (haiku) → `research.json` (give it the day's `theme` and ResearchHistory topic_keys to avoid) →
   `python -m studio record-research --video-id … --file research.json` (fix and retry once on errors).
   After EVERY step from here on: `python -m studio checkpoint --video-id …` (saves work to Drive so
   the next session can continue if this one stops).
5b. `python -m studio plan --video-id … --category <cat>` (experiment arm 70/20/10, style from the
   registry that suits the topic and was not used recently, story_structure + hook_type). Give its output to
   the storywriter; after writing run `python -m studio plan --video-id … --category … --storyboard storyboard.json`
   (writes the explicit style and runs the originality check; a block means rewrite, not override).
6. **storywriter** (sonnet) → `storyboard.json` + `metadata.json` →
   `python -m studio validate-storyboard --file storyboard.json` (add `--document-based` for docs).
   Every video should have 1–2 `image` scenes (licensed internet photos):
   `python -m pip install -q -r requirements-vision.txt` (if not installed), then
   `python -m studio resolve-images --storyboard storyboard.json`
   (internet images: CC0/PD/CC BY only, checked locally by OmniVision; owner images from Drive
   `References/Images` via `fetch-asset`). Unresolved scenes become title cards automatically.
7. **quality-reviewer** (sonnet) → `review.json`. If `fail`, one revision by storywriter, review again.
   Still failing → stop and report (item stays in its state for a human).
7b. Director pass (you, Opus, are the director): for the 2–4 most important scenes write a short
   shot brief (second-by-second visuals, layout, motion, colours, text, timing from the narration)
   (apply `insights.style_notes` and docs/STYLE_REFERENCE.md; vary layouts from yesterday's video;
   When the plan's style uses `stickman` scenes, check they act the narration — fix poses,
   faces and timing in storyboard.json yourself; use custom scenes for the evidence/diagrams)
   and set those scenes' visual to `{"type": "custom", "code": "sceneN"}` (add `"full_frame": true`
   for big diagrams). Send each brief to the **scene-coder** (Sonnet) agent with the video id, the
   scene name and its id prefix `s<index0>-` (scene 1 → `s0-`). Style reference: docs/STYLE_REFERENCE.md.
7d. PER-SCENE LOOP (rules.md §4, after the voice exists): `python -m studio scene-frames --video-id …
   --storyboard storyboard.json` (3 stills per scene, no full render) → **scene-reviewer** (Sonnet)
   for each scene strip with that scene's JSON (run several in parallel; batch 4–6 scenes per
   agent for long videos to save tokens) → apply every fix to storyboard.json / scene code yourself
   → re-run `scene-frames --scene N,M` only for changed scenes → at most 2 rounds per scene.
   Only then the full render (step 8). The thumbnail is made automatically by `submit`.
7c. Voice first, separately: `python -m studio voice --video-id … --storyboard storyboard.json`
   (Google Chirp 3 HD within the free tier: ≤ 1,000 chars per Short, ≤ 8,000 per long video,
   monthly cap 600k ≤ 90 % of the 1M free characters; clips are cached by exact text so re-renders
   and resumes cost 0 characters; over budget → free offline Kokoro voice for the whole video).
8. `python -m studio render --video-id … --storyboard storyboard.json` (QC must be ok; it re-uses
   the cached voice). The HyperFrames
   engine (animated HTML/GSAP: transitions, kinetic text, particles, light sweeps, Ken Burns images)
   is used automatically; if it fails the Pillow renderer takes over (`engine` in the output).
   Needs `npm ci --prefix renderer/hyperframes` once per container.
   Director review: `python -m studio frames --video-id …` → look at `review_sheet.png` (one image,
   2 frames per scene) and `custom_scenes` (any `fallback` means the camera man's code was rejected,
   with the reason). If a scene is wrong (unreadable, overlapping, off-brief, rejected), YOU fix the
   code in runtime/work/<id>/scenes/ directly (or re-brief the scene-coder once) and render again.
   At most 2 review rounds; then submit the best version.
   Voice: Google TTS → Kokoro (offline) → Piper; one voice per video. Adds music (ducked), SFX
   and the automatic subscribe end card. Image/SFX/music credits are added to the description.
8b. `python -m studio qc --video-id …` (decode, black/frozen frames, resolution, A/V sync, LUFS/true peak,
   speech-over-music, ASR WER). `submit` repeats it and refuses any critical result.
9. `python -m studio submit --video-id … --metadata metadata.json --review review.json`.
   Apps Script emails a neat HTML approval request (poster + Drive preview button). The approver
   replies `APPROVED VIDEO-…` before the evening slot (19:30 IST − 15 min) and the Apps Script uploads
   it scheduled for 19:30 IST; no reply = skipped. Uploading never needs Claude. After a successful upload the
   video file is moved to the Drive `Archive` folder (Apps Script; `reconcile` repeats it if needed).
9b. Tamil version (same run, right after the English submit; `discover` also returns
   `"action": "localize"` if a run stopped before it): **localization-editor** (Opus) reads the English
   texts only (`python -m studio texts --storyboard storyboard.json --out texts.en.json`) plus
   metadata.json and writes texts.ta.json + metadata.ta.json (natural spoken Tamil with English
   tech words) → `python -m studio localize --video-id <English id> --texts texts.ta.json --metadata metadata.ta.json`
   (same scenes as English, only the text changes)
   (new id: Short 051–099 / long 151–199; copies assets and custom scenes) → `voice` (Tamil
   Chirp 3 HD Charon, same narrator; WaveNet Tamil fallback, both free tiers) → `render` →
   `frames` (check Tamil text renders, nothing overflows; shorten text in the storyboard if it
   does) → `submit` (Tamil Short 21:00 IST, Tamil long 18:30 IST). Skip the quality-reviewer for the
   translation (facts are unchanged); the approver approves it separately.
   If usage is short (`go_lean`/`wait`), the English video always comes first.
10. Optionally: storywriter writes Reddit drafts JSON → `python -m studio reddit-drafts …`
    (drafts only; never post).

Finish with a 5-line summary: item, state, title, warnings, next step. Keep model output compact;
pass JSON files between agents rather than long prose.

## Long-form workflow (daily 03:47 IST scheduled task; 16:9 system-design explainer, 3–4 min)
Same steps, rules and approval gate as the Short, with these differences:
- `python -m studio discover --format long` → `resume` / `none` / `research` with `theme.topic` (the
  next system-design syllabus topic, or a ThemePlan row `long: <topic>`). `new-item --format long`
  gives an id `VIDEO-YYYYMMDD-101…` (long ids are > 100; the approval reply format is unchanged).
- researcher: pass the exact topic and "format long" (4–8 sources, 12–25 facts).
- storywriter: "format long" section of its instructions (hook in 15 s, a new open question every
  60–90 s, chapters, 35–70 scenes, no #Shorts). `validate-storyboard` applies the long rules.
- Director pass (you): 6–12 custom scenes for the key diagrams (canvas 1920×1080, see
  scene-coder). Send briefs to scene-coder agents (several in parallel), then render.
- `render` takes ~20–40 min (HyperFrames only, no Pillow fallback). Review with `frames`
  (one sheet; look closely at custom scenes), fix code yourself, at most 2 rounds.
- `submit` adds YouTube chapters (0:00 …) from the render timings and picks the 17:30 IST slot
  (`LONG_PUBLISH_TIME`). The Apps Script uploads files > 45 MB in resumable 32 MB chunks.
- Budget: if `budget` says go_lean, still make the video but use 4–6 custom scenes and one review.
- Thumbnails are not set by the API here; YouTube picks a frame (the hook frame is designed for it).
- Then the Tamil long version (step 9b; long ids 151–199, slot 18:30 IST).

