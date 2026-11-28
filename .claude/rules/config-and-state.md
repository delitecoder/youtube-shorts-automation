---
paths: ["config/**", "src/studio/core/**", "src/studio/orchestration/**", "src/studio/storage/**"]
---
- Protected flags (DRY_RUN, UPLOAD_ENABLED, PUBLICATION_ENABLED, PROMOTION_ENABLED) are env-only; YAML may never set them.
- Keep the external Sheets contract (tabs, columns, states, id ranges) backward compatible; add tabs, never rename.
- Checkpoints fail closed: a failed read-back verify must block destructive actions.
