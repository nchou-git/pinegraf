# Claims Browse

Claims is a top-level area because claims cut across sources. Users usually look
for a claim by entity, predicate, confidence, or status, not by the crawl that
produced it.

`/api/claims` supports exact predicate and entity filters, a source evidence
filter, confidence threshold, status filter, text search, and pagination.
`status=current` means `valid_to is null`; `status=superseded` means
`valid_to is not null`; `status=all` includes both.

The entity detail page reuses the same claims list component with subject and
object filters applied in both directions. Source detail links to the global
Claims page with a source filter instead of adding a source-local claims tab.
