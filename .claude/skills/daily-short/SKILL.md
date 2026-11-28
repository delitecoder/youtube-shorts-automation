---
name: daily-short
description: Produce today's English YouTube Short (then its Tamil version) end to end with the studio CLI. Use for the 10:07 IST scheduled run or when the owner asks for today's Short.
---
1. Read rules.md, then follow docs/OPERATIONS.md "Daily workflow" exactly (budget → doctor → reconcile → audit → discover).
2. New item: researcher → `record-research` → `plan --category` → storywriter (give it the plan) →
   `plan --storyboard` (style + originality) → `validate-storyboard` → images → review → director pass →
   `voice` → `scene-frames` + scene-reviewer → `render` → `qc` → `submit`.
3. `checkpoint --video-id` after every step. Stop on any QC/originality block; never override it.
4. Then the `localize` skill. Finish with the 5-line summary.
