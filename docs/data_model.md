# Data Model

## Entities

`entities` is the identity table. Rows represent a `person` or `organization`.
The `id` UUID is authoritative; names are labels, not identity keys.

`entity_aliases` stores normalized aliases for matching. Aliases are lowercased
and whitespace-normalized. Multiple entities may share the same alias; the
resolver only reuses one when context produces exactly one candidate.

`entity_attributes` stores claim-level attributes such as `current_company`,
`current_title`, `past_company`, `education`, `class_year`, and `bio_summary`.
Each row carries confidence, validation verdict, extraction time, and optional
source URL. These rows are source-linked claims, not necessarily the final
canonical profile.

## Raw Pages

`raw_pages` stores source snapshots. `source_url`, cleaned `page_text`, fetch
metadata, `content_sha256`, and gzip-compressed `raw_html_gz` preserve what was
retrieved. `parsed_at` controls parse idempotency. `entity_id` links the snapshot
to the subject entity; legacy `alum_name` remains for compatibility but is not
authoritative.

## Structured Evidence

`claims` is the claim-native parser output. Each row has an explicit subject,
predicate, object name/value, source page, confidence, and text evidence. The
parser no longer defaults relationships to the page entity.

`facts`, `connections`, and `projects` are projection tables linked to
`source_raw_page_id`. New explicit connections and projects are written from
resolved claims, carry `entity_id` for the claim subject, and keep legacy
`alum_name` columns until a later cleanup.

`alumni_profiles` is a derived projection synthesized from structured evidence
and attributes. It is useful for UI and strict query context, but source-linked
claims in `entity_attributes` and evidence tables are more authoritative.

## Audit Events

`audit_events` is append-only. It records actor, action, redacted request
payload, and `created_at`. There is no `updated_at`; audit rows are never
modified. Actor/action time indexes support admin filtering and cursor-based
listing.

## Authority

- Authoritative identity: `entities.id`
- Authoritative source text: `raw_pages` snapshots
- Source-linked claims: `claims`, `entity_attributes`, `facts`, `connections`, `projects`
- Derived profile view: `alumni_profiles`
- Operational history: `audit_events`
