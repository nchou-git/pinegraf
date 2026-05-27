# Reliability Design

Pinegraf enforces five pipeline reliability goals:

- Every successful fetch is either linked to a document, marked unchanged from a prior fetch, or explicitly skipped with a reason.
- Source rows, source detail, and top stats use canonical SQL from `backend/db/stats_queries.py`.
- Parse runs freeze their pending fetch scope at `snapshot_at`; concurrent crawl output waits for the next parse.
- Admins can run source integrity checks from the source config page.
- Maintenance self-heals stale runs, dangling document links, and drifted source counters.

Insert paths enforce the cheap invariants. The crawl runners write hash-diff metadata
for unchanged bodies. The parse runner freezes `total_to_parse` and only processes
that frozen ID list. Detection lives in `verify-integrity` and maintenance.

Maintenance repairs only low-risk drift: stale running jobs, dangling
`DocumentFetch` rows, and denormalized counter mismatches. Broken unchanged-body
chains and old orphan documents are audited for review rather than deleted.

Source rows show one mutually exclusive state: active run progress, pending parse
work, due for re-crawl, or up to date.
