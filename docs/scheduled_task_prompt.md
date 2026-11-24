Daily YouTube Shorts production run (fresh cloud session).

1. Get the code: if a `youtube-shorts-automation` checkout exists (the environment setup script
   makes one), `cd` into it and `git pull --ff-only`; otherwise
   `git clone --depth 1 https://github.com/delitecoder/youtube-shorts-automation.git` and `cd` into it.
   If that fails or the repository is empty, stop and report "repository not available".
2. Install if not already done: `pip install -q -r requirements.txt` and `bash scripts/fetch_kokoro.sh`
   (and `pip install -q -r requirements-vision.txt` only if the storyboard has image scenes).
3. Run `PYTHONPATH=src python -m ytauto doctor`. If `setup_complete` is false or `ok` is false,
   stop immediately and reply with one line naming what is missing (see docs/SETUP.md). Do not
   continue in that case — this keeps unconfigured runs cheap.
4. Otherwise follow the "Daily workflow" in CLAUDE.md exactly: Opus orchestrates, researcher and
   quality-reviewer (Haiku), storywriter (Sonnet), advisor (Fable) only for a genuinely hard
   decision. Use the `python -m ytauto` commands for all deterministic work.
5. Never change flags, never upload or publish, never print key values, never edit code during the
   run. Stop on any error you cannot fix by correcting the JSON inputs once.
6. End with a five-line summary: video ID, state, title, warnings, next step for the owner
   (normally: reply YES/NO to the approval email).
