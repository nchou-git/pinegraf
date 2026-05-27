# Temporal Design

Today Pinegraf captures timeline raw material: every new document and every new
promoted claim gets a `valid_from` timestamp based on the parse run snapshot.
`valid_to` and supersession links stay empty until reconciliation exists.

We do not yet infer that claim X supersedes claim Y. That requires comparing
`(subject, predicate)` pairs across page versions and deciding whether a new
value replaces the old value or coexists. For example, "former dean" and
"current dean" can both be true.

Until supersession logic lands, the UI continues to show the most recent
projected graph data. Older claims remain in raw/audit views and storage, but
entity summaries are still rebuilt from the current scoring/projection rules.

Roadmap:

1. Capture timeline data on documents and claims. This pass.
2. Add supersession heuristics during rebuild.
3. Add a UI timeline view.
4. Add temporal queries such as "as of date X."
