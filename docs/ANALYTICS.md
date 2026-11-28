# Analytics, experiments, playlists

- `studio audit --email`: daily views audit → guidance for the storywriter. `studio peak-time`: weekly slots.
- `studio facts`: VideoFacts rows per video. **A metric that is not available is stored as unknown, never 0**
  (impressions and CTR are not available with the current API scopes). Shorts/long and en/ta are separate.
- `studio experiment list|evaluate`: one variable at a time; deterministic 70 % proven / 20 % variation /
  10 % exploration; balanced arms; residuals against the same format+language median; bootstrap CI; at least
  5 per arm. Inconclusive results are never winners. A concluded result writes a Recommendation with its
  evidence, which `studio plan` then uses.
- `studio playlist`: one subject playlist per language, created only once ≥ 2 videos exist; earlier videos are
  backfilled. The Apps Script finds playlists by the marker `#studio-playlist:<key>` and adds each video once.
