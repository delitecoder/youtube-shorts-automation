# Model routing

Source of truth: `config/models.yaml` (`python -m studio route --task <task>`). Route by task, not by habit.
Deterministic work (analytics maths, QC, normalisation, state, scheduling, similarity, validation) is Python only.

| Agent | Model | Task |
|---|---|---|
| main session (orchestrator / director) | Opus | runs the workflow, director briefs, final decisions |
| researcher | Haiku | research JSON (escalates to Sonnet on validation failure) |
| research-verifier | Sonnet | re-checks high-impact facts |
| editorial-director | Opus | story plan / angle when unclear |
| storywriter | Sonnet | storyboard + metadata |
| technical-reviewer, quality-reviewer | Sonnet | correctness and quality verdicts |
| visual-director, scene-coder, scene-reviewer | Sonnet | visuals, HTML/GSAP scenes, scene review |
| audio-director, audio-engineer | Sonnet | sound plan, audio QC diagnosis |
| localization-editor | Opus | Tamil version (owner choice 2026-11-25: most accurate for Tamil) |
| analytics-scientist, experiment-scientist | Sonnet | analytics guidance, experiments |
| final-qc-director, code-reviewer, code-engineer | Sonnet | pre-submit check, code review and fixes |
| studio-architect, release-manager | Opus | structural decisions, merges |
| advisor | Fable | rare hard decisions; needs credits, so normally unused |

Escalation: at most 2 hops, only for `validation_failed` or `low_confidence`; never silently upgrade a tier.
The usage budget (`studio budget`) decides go_full / go_lean / wait / weekly_stop (docs/OPERATIONS.md).
