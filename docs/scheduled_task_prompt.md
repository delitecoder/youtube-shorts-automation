Daily YouTube Shorts production run (fresh cloud session). Also used by the 13:37 IST catch-up run.

1. Get the code: if a `youtube-shorts-automation` checkout exists, `cd` into it and `git pull --ff-only`;
   otherwise `git clone --depth 1 https://github.com/delitecoder/youtube-shorts-automation.git` and `cd` into it.
2. Install if missing: `pip install -q -r requirements.txt`, `bash scripts/fetch_kokoro.sh`,
   `npm ci --prefix renderer/hyperframes --silent` (HyperFrames renderer).
3. `PYTHONPATH=src python -m ytauto doctor`. If `setup_complete` is false or `ok` is false, stop and
   reply with one line naming what is missing.
4. Follow the "Daily workflow" in CLAUDE.md exactly. `discover` decides: `resume` (continue an
   unfinished video from its checkpoint), `none` (stop: today's video is done or waiting for
   approval), or a new item. Run `checkpoint` after every step so a later session can continue.
   Opus orchestrates; researcher + quality-reviewer = Haiku, storywriter = Sonnet, advisor = Fable
   (only for a genuinely hard decision). Each video: 1–2 licensed internet images, HyperFrames
   animation, SEO metadata (title ≤ 60 chars, 3–5 hashtags incl. #Shorts, 8–15 tags, category).
5. Never change flags, never upload or publish (the Apps Script uploads only after the approver's
   APPROVED reply), never print key values, never edit code or push during the run.
6. End with a five-line summary: video ID, state, title, warnings, next step for the owner.
