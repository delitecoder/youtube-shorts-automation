Run today's YouTube Shorts production cycle for the repository youtube-shorts-automation.

1. cd into the repository and follow the "Daily workflow" in CLAUDE.md exactly, using the
   researcher, storywriter and quality-reviewer subagents for their steps and the `python -m ytauto`
   commands (PYTHONPATH=src) for everything deterministic.
2. Respect every hard rule in CLAUDE.md. In particular: do not change any flag, do not try to
   upload or publish, and stop as soon as a command reports `"action": "none"` or `ok: false`
   that you cannot fix by correcting the JSON inputs once.
3. Do not edit code during the scheduled run. If a command fails because of a code defect, stop
   and describe the failure.
4. End with a five-line summary: video ID, state, chosen title, warnings, and what the owner
   needs to do next (normally: reply YES/NO to the approval email).
