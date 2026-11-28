---
name: recover
description: Resume after an interruption (usage limit, compaction, new container). Use when a session starts mid-run or the owner says "continue".
---
1. `python -m studio resume` (restores runtime/state from the RunState tab; prints next actions).
2. `python -m studio state verify`; if unverified, `state save` until it verifies — do nothing destructive before.
3. Video work: `python -m studio discover` → `resume` → `restore --video-id <id>` → continue from `next_step`.
See docs/RECOVERY.md.
