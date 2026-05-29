# Claims Browse

Claims is a top-level area because claims cut across sources. Users usually look
for a claim by entity, predicate, source, or status, not by the crawl that
produced it.

`/api/claims` supports exact predicate and entity filters, a source evidence
filter, status filter, text search, and pagination.
`status=current` means `valid_to is null`; `status=superseded` means
`valid_to is not null`; `status=all` includes both.

The entity detail page reuses the same claims list component with subject and
object filters applied in both directions. Source detail links to the global
Claims page with a source filter instead of adding a source-local claims tab.

## Conflicts

Conflicts is the parent surface for anything the system has detected that needs
human judgment. It has two queues:

- Contradicting Facts: the claims layer found sources that disagree about the
  same fact. Admins pick the correct claim or mark both as valid.
- Ambiguous Identity: entity resolution found two records that may refer to the
  same entity. Admins merge, confirm the split, or defer.

Both queues follow the same review pattern: inspect the evidence, accept or
reject the proposed resolution, and keep an audit trail of the decision.
