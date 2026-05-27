# Run Stop And Start Again Semantics

Pinegraf does not implement true in-process pause/resume for crawl or parse jobs.
The user-facing `Stop` action cancels the active Cloud Run execution and marks
the `SourceRun` as `stopped`.

`Start again` creates a new `SourceRun`:

- Crawl starts a new crawl run while preserving source-level cumulative counters.
- Parse starts a new parse run with a fresh `snapshot_at` at job start and the
  default `scope="unparsed"`, so already-linked `DocumentFetch` rows are skipped.
- Any prior stopped run for the same source and kind is marked `superseded` and
  audited with `run.superseded_by_resume`.

True pause would require persisting the crawl frontier or parse work queue and
resuming the same run from that snapshot. That is intentionally out of scope for
the current implementation.
