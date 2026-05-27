# Content Versioning Gap

When a crawl re-fetches an existing URL with changed content, Pinegraf stores a
new `Fetch` row with the new `content_hash`. The next parse can create a new
`Document` for that hash because the new fetch has no `DocumentFetch` link.

Current gap: the previous `Document` for the same URL is not marked superseded,
retracted, or replaced. Its claims and entity evidence remain active alongside
the newer document. This can eventually create duplicate or conflicting claims
for URLs whose content changes over time.

TODO: design URL-level document versioning before changing behavior. The design
needs to decide whether supersession is keyed by canonical URL, redirect-final
URL, source ownership, or a normalized URL identity, and how claim/evidence
status should change when older content is replaced.
