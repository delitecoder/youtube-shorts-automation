Daily LONG-FORM YouTube video run (fresh cloud session). One 16:9 system-design explainer per day.

1. Get the code: if a `youtube-shorts-automation` checkout exists, `cd` into it and `git pull --ff-only`; otherwise `git clone --depth 1 https://github.com/delitecoder/youtube-shorts-automation.git` and `cd` into it.
2. Install if missing, always with `python -m pip`: `python -m pip install -q -r requirements.txt`, `bash scripts/fetch_kokoro.sh`, `npm ci --prefix renderer/hyperframes --silent`.
3. `PYTHONPATH=src python -m ytauto doctor`. If `setup_complete` or `ok` is false, stop with one line naming what is missing.
4. Usage budget first (CLAUDE.md "Usage budget"; `budget --unknown` when usage tools are missing). wait → checkpoint and stop; weekly_stop → stop.
5. Follow "Long-form workflow" in CLAUDE.md: `discover --format long` → resume / none / research. `checkpoint` after every step. You (Opus) are the director: brief 6–12 key diagrams to scene-coder agents (Sonnet), review the rendered frames, fix the scene code yourself if anything is wrong (max 2 rounds). researcher + quality-reviewer = Haiku, storywriter = Sonnet.
6. Safety net, only if `send_later` exists: one reminder (delay_minutes 310) to continue after the usage reset; delete it when the run ends normally.
7. Never change flags, never upload or publish (the Apps Script uploads only after the approver's APPROVED reply), never print key values, never edit code or push during the run. If a step fails because of a code defect, stop, checkpoint, and describe it.
8. End with a five-line summary: video ID, state, title, budget decision, next step for the owner.
