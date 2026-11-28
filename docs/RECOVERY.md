# Recovery

Durable state lives in Sheets; `runtime/state/*` is only a cache.

- `python -m studio state save|verify|show`: the checkpoint is written locally and to the RunState tab, read
  back and compared by sha256. A failed verify raises and leaves `CHECKPOINT_UNVERIFIED`, which blocks
  push / publish / delete until a verified save succeeds (fail closed).
- `python -m studio resume`: restores `runtime/state/*` from the durable store in a fresh container and prints
  the next actions. The SessionStart hook does this automatically; PreCompact re-saves before compaction.
- Video work: `python -m studio discover` returns `resume`; `restore --video-id` pulls the work files from Drive
  (saved by `checkpoint --video-id` after every step) and reports `next_step`.
- Usage limits: a single `send_later` reminder 310 min ahead resumes the run (docs/OPERATIONS.md).
- Upload errors end in NEEDS_MANUAL_REVIEW and are never retried automatically.
