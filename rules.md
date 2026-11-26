# rules.md — the owner's rules and requirements (READ-ONLY)

**Claude must never modify this file.** Only the owner edits it. Every Claude session (interactive
or scheduled) reads it before producing anything, and it wins over any other doc except safety
rules in CLAUDE.md "Hard rules". `tests/test_rules_file.py` fails if the file changes without the
owner updating its recorded checksum.

## 1. What the channel makes (daily, automatic, laptop off)
- Every day: one **Short** (vertical, 45–75 s) and one **long video** (16:9, 6–8 min), each in
  **English and Tamil** (4 videos/day). The Tamil version reuses the English scenes; only text and
  voice change. Never more than that per day.
- Shorts: the most trending tech topic of the day (tech news / trending search / quick system
  design / true tech story). Long videos: system design for everyone. Never repeat a story.
- Everything runs in the cloud on the scheduled tasks; nothing needs the owner's laptop.

## 2. Story first (every video, remember it whenever a video is made)
- **Pattern: Question → Problem → Solution.** Scene 1 asks a real question the viewer cares about
  (ends with "?"). Then the problem, shown as a story with characters. Then the solution, step by
  step. End with a short recap and one question for the comments.
- It is a **story**: the same characters live in the same world, want something, hit a problem and
  solve it. Every concept is something a character does, sees or uses.
- **Simple words.** A person who never studied computer science must understand every sentence.
  No jargon without an everyday picture first ("a load balancer is like the guard who sends each
  customer to the shortest queue"). Never talk down to the viewer, never baby talk.
- **Tamil:** natural spoken Tamil the way people really talk, Tanglish welcome (common English
  tech words stay in English: app, server, cache, UPI, login…). Never formal/literary Tamil.

## 3. Look and motion: a real, consistent animated stickman world
- Fully animated **stickman** videos: a fixed **cast** (same characters, same look, every video)
  living in a fixed **world** (same places, same style), with animated backgrounds (moving cars,
  clouds, blinking servers, ticking clocks, people in the background), camera moves and scene
  transitions that follow the story.
- Each video may add new props/places, but the cast and the world style stay consistent.
- 3D (three.js) and motion-graphic scenes are allowed when they explain best, inside the same world.
- Must feel like a polished AI-era animated video **without AI slop**: no generic stock phrases,
  no pulsing/floating filler motion, no random particles, no meaningless glow, no repeated
  identical entrances, no AI-generated images that look fake. Every element on screen earns its
  place and moves for a reason.
- Videos must not all look the same: vary locations, camera, palettes and caption styles day to day.
- Proper **sound**: background music that fits the mood (ducked under the voice), sound effects on
  real actions (footsteps, door, typing, notification, crowd, server hum, success/fail stingers),
  ambience for each location. Licensed sounds only (CC0 / CC BY with credits in the description).

## 4. How a video is made (pipeline)
1. Analyse previous analytics first (`discover` → insights): what viewers searched, what worked,
   what failed. Plan the topic, hook and title from it.
2. Research (sources), then the story script (storyboard) in the Question → Problem → Solution pattern.
3. For **each scene**: write the scene (code/storyboard) → animate → snapshot frames →
   **scene reviewer** checks it (story, characters, readability, motion, sound cues, no slop) → fix
   → only then the next scene. Full render after all scenes pass.
4. Voice (Google Chirp 3 HD free tier; Tamil: same narrator), music, SFX, captions.
5. Thumbnail: designed per video (main character + 3–5 big words + the world), uploaded with the video.
6. Submit for the owner's email approval. Nothing is published without APPROVED.
7. YouTube "altered or synthetic content" disclosure is turned ON for every upload (owner choice).

## 5. Reach and engagement
- Hashtags and tags: reuse the ones that brought views on the owner's previous videos (ranked
  from the channel's own history by the audit: #shorts #viral #trending #viralvideo
  #trendingshorts #aishorts #aivideo #ai, #tamil for Tamil) + 2–4 real topic hashtags. Tags
  always include shorts, viral, trending. Other platforms' names (#tiktok, #fyp, #reels,
  #instagram) are not used.
- Engagement is very low (≈1.3 % likes, almost no comments), so every video ends with ONE
  specific, easy question the viewer can answer in a word or two (spoken + on screen), and the
  same question is the pinned comment. The story gives viewers an opinion to share.
- Titles: the viewer's own search words first, curiosity + clarity, no clickbait lies.
- Publish slots (IST): long English 17:30, long Tamil 18:30, Short English 19:30 (or learned peak),
  Short Tamil 21:00.

## 6. Cost, tokens and code quality
- Free services only (Google TTS free tier, YouTube API quota, open-source tools, licensed free
  assets). No paid API without the owner's written approval in chat.
- Use tokens as needed but optimised: Python for deterministic work; the cheapest model that does
  the job well (Haiku research/review, Sonnet writing/coding, Opus directing/translation).
- Code changes follow the **ponytail** skills (.claude/skills/ponytail*): smallest change that fully
  solves the task, reviewed before commit; tests pass before pushing.

## 7. Safety (never break)
- Nothing is uploaded or published without the owner's explicit email approval.
- Never use the owner's passwords, cookies or browser sessions from the cloud; official APIs only.
- Never print, log, email or commit secrets. The public repo never contains personal data.
