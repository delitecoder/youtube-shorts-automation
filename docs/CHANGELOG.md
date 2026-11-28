# Changelog

## 2026-11-28 — studio rebuild
- Package `src/ytauto` → `src/studio` (modular domains; `python -m ytauto` kept as a shim). Backup branch
  `archive/pre-rebuild-20261128-0237`.
- YAML config, model router, JSON logs, usage ledger, resource probe, durable sha256-verified checkpoints,
  Claude Code guard hooks.
- Audio: cue sheet with reasons, 4 stereo stems, dialogue-anchored mix, mastering, audio QC, ASR WER,
  speech-aligned captions.
- Video: QC (decode / black / frozen / resolution / sync), render manifest + cache, style registry with novelty.
- Editorial: topic gate, research contract (fact ids, status, access dates), schema-2 scene contract.
- Intelligence: content-memory originality gate, VideoFacts (unknown ≠ 0), controlled experiments,
  playlists, notification templates.
- Apps Script 2026.11.28-1 (owner deploys): category default, playlists, checks line, honest webhook status.
- rules.md rewritten (owner-authorised), concise CLAUDE.md, 20 agents, project skills, docs.
