---
name: localize
description: Make the Tamil version of a submitted English video (discover → "localize").
---
`python -m studio texts --storyboard storyboard.json --out texts.en.json` → localization-editor agent writes
texts.ta.json + metadata.ta.json → `python -m studio localize --video-id <EN id> --texts texts.ta.json
--metadata metadata.ta.json` → `voice` → `render` → `frames` (Tamil text renders, nothing overflows) → `qc` → `submit`.
No quality-reviewer pass (facts unchanged); the approver approves it separately. English always comes first.
