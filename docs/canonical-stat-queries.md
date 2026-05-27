# Canonical Stat Queries

Canonical source stats live in `backend/db/stats_queries.py`.

- `pages_fetched`: distinct successful URLs for a source with either stored body
  bytes or a resolved unchanged-body chain.
- `urls_known`: distinct URLs ever attempted for the source.
- `documents_for_source`: distinct documents linked through
  `DocumentFetch -> Fetch -> SourceRun`.
- `claims_for_source`: distinct claims with evidence that joins through
  `ClaimEvidence -> ClaimRaw -> Chunk -> Document -> DocumentFetch -> Fetch -> SourceRun`.
- `entities_for_source`: entities mentioned by raw claims in documents linked to
  the source.
- `pending_parse_count`: successful body-bearing fetches with no `DocumentFetch`
  link and no skip marker.

Do not inline these definitions in API handlers or runners. Denormalized
`Source.pages_fetched_total` and `Source.urls_known_total` are periodically
refreshed from the same functions and corrected by maintenance.
