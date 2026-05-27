# Run Pause And Resume Semantics

Pinegraf does not implement true in-process pause/resume for crawl or parse jobs.
The user-facing `Pause` action cancels the active Cloud Run execution and marks
the `SourceRun` as `stopped`.

`Resume` creates a new `SourceRun`:

- Crawl starts a new crawl run while preserving source-level cumulative counters.
- Parse starts a new parse run with a fresh `snapshot_at` at job start and the
  default `scope="unparsed"`, so already-linked `DocumentFetch` rows are skipped.
- Resume clears all prior stopped runs for this source and kind, not just
  the latest. Each cleared run is marked `superseded` and audited with
  `run.superseded_by_resume`.

Parse is never auto-triggered by crawl or parse completion. Users explicitly
click `Parse` when pending documents should be processed.

True pause would require persisting the crawl frontier or parse work queue and
resuming the same run from that snapshot. That is intentionally out of scope for
the current implementation.
