# Graph Schema

This document describes the current Pinegraf graph schema. ORM definitions live
in `backend/db/models.py`; Alembic migrations are the source of upgrade and
downgrade behavior.

## Identity Tables

### `entities`

Canonical identity table. `entities.id` is the identity key.

- `id` UUID primary key.
- `entity_type` text constrained to `person` or `organization`.
- `canonical_name` display label.
- `name_embedding` vector(1536) on Postgres, JSON in SQLite tests.
- `context_embedding` vector(1536) on Postgres, JSON in SQLite tests.
- `created_at`, `updated_at` timezone-aware timestamps.

### `entity_aliases`

Alternate labels for matching and enrichment.

- `id` integer primary key.
- `entity_id` foreign key to `entities.id`, cascade delete.
- `alias` normalized alias string.
- `source` source identifier that produced the alias.
- Unique constraint: `(entity_id, alias)`.

### `entity_attributes`

Source-linked claims about entities. These rows are claims, not final truth.

- `id` integer primary key.
- `entity_id` foreign key to `entities.id`, cascade delete.
- `attribute_name` constrained to current company/title, education, class year,
  alumni spreadsheet fields, Wikidata DOB/occupation/notable fields.
- `attribute_value` text claim value.
- `source` required source identifier such as `alumni_xlsx_v2`,
  `raw_page:<id>`, or `wikidata:<qid>`.
- `source_url` optional human-readable source URL.
- `as_of_date` optional source effective date.
- `confidence` constrained to `high`, `medium`, or `low`.
- `extracted_at` timezone-aware timestamp.
- `last_verified_at` nullable timestamp for sources that can be refreshed.
- `validation_verdict` constrained to `keep`, `uncertain`, or `drop`.

Indexes support entity/name lookup and source filtering.

## Source Snapshot Tables

### `raw_pages`

Fetched page snapshots and parse status.

- `id` integer primary key.
- `alum_name` legacy subject label.
- `entity_id` nullable foreign key to `entities.id`.
- `source_url` fetched URL.
- `page_title` page title.
- `page_text` cleaned text used by parser and retrieval.
- `fetched_at` timezone-aware timestamp.
- `parsed_at` nullable timestamp; null means available for parse.
- `content_sha256` optional fetched content hash.
- `http_etag`, `http_last_modified`, `http_status` fetch metadata.
- `raw_html_gz` compressed raw HTML snapshot, nullable.

### `host_boilerplate`

Per-host cleanup model used when rebuilding `raw_pages.page_text`.

- `host` text primary key.
- `prefix` repeated leading text to strip.
- `suffix` repeated trailing text to strip.
- `updated_at` timezone-aware timestamp.

### `page_chunks`

Chunk-level retrieval and embedding table.

- `id` integer primary key.
- `raw_page_id` foreign key to `raw_pages.id`, cascade delete.
- `chunk_index` zero-based chunk index within the page.
- `text` chunk text.
- `embedding` vector(1536) on Postgres, JSON in SQLite tests.
- `created_at` timezone-aware timestamp.
- Unique constraint: `(raw_page_id, chunk_index)`.

Postgres migrations also add trigram GIN indexes on `raw_pages.page_text` and
`entity_attributes.attribute_value`.

## Extracted Evidence Tables

### `claims`

Claim-native extraction table. This is the parser's authoritative graph output:
every row names its own subject and predicate instead of inheriting a page
subject.

- `id` integer primary key.
- `subject_entity_id` nullable foreign key to `entities.id`.
- `subject_name` subject surface form copied from extraction.
- `predicate` normalized relationship or attribute predicate.
- `object_entity_id` nullable foreign key to `entities.id` when the object is an
  entity.
- `object_name` nullable object surface form for entity-like objects.
- `object_value` nullable literal value for attribute-like claims.
- `object_type` text type such as `person`, `organization`, `project`,
  `education`, `role`, `location`, `date`, or `text`.
- `source_raw_page_id` required foreign key to `raw_pages.id`.
- `source_chunk_id` nullable foreign key to `page_chunks.id`.
- `source_chunk_index` nullable chunk index retained even if chunk rows are
  rebuilt.
- `text_evidence` verbatim supporting phrase.
- `confidence_score` nullable 0.0-1.0 confidence.
- `prompt_version` extraction prompt/schema version.
- `validation_verdict` keep/uncertain/drop.
- `created_at` timezone-aware timestamp.

Connections and projects are projections of resolved claims. A connection is not
written unless both endpoints were explicit in the claim and resolved to
entities.

### `facts`

Parsed evidence that does not fit a first-class edge or project row.

- `id` integer primary key.
- `alum_name` legacy subject label.
- `entity_id` nullable subject entity foreign key.
- `source_raw_page_id` required foreign key to `raw_pages.id`.
- `category` text category; position facts use `position`.
- `content` text, often JSON for structured position rows.
- `confidence` low/medium/high label.
- `confidence_score` nullable 0.0-1.0 model score.
- `text_evidence` verbatim chunk phrase supporting the fact.
- `validation_verdict` keep/uncertain/drop.

### `projects`

Parsed project mentions tied to source evidence.

- `id` integer primary key.
- `alum_name` legacy subject label.
- `entity_id` nullable subject entity foreign key.
- `source_raw_page_id` required foreign key to `raw_pages.id`.
- `project_name` project label.
- `description` extracted description.
- `confidence_score` nullable 0.0-1.0 model score.
- `text_evidence` verbatim chunk phrase supporting the project.
- `validation_verdict` keep/uncertain/drop.

### `connections`

Graph edge projection table. It stores both explicit claim-derived edges and
inferred edges.

- `id` integer primary key.
- `alum_name` legacy left-side label; for new explicit edges this is the claim
  subject name.
- `entity_id` nullable left-side entity foreign key.
- `connected_entity_id` nullable right-side entity foreign key.
- `connected_name` right-side display label.
- `source_raw_page_id` nullable raw page source.
- `context` human-readable edge context.
- `relationship_type` explicit type or inferred typed token.
- `confidence_score` nullable 0.0-1.0 model or rule confidence.
- `text_evidence` verbatim chunk phrase for claim-derived explicit edges.
- `is_inferred` true for reconciliation-generated edges.
- `derivation` rule explanation for inferred edges.
- `source_ids` JSON list of source row identifiers used by inferred edges.
- `validation_verdict` keep/uncertain/drop.

Explicit parser relationship types come from the extraction output and default
to `associate` when the model does not provide a better type.

## Derived Tables

### `alumni_profiles`

Legacy/profile projection used by Lookup and strict query context.

- `id` integer primary key.
- `name` legacy display name.
- `entity_id` nullable foreign key to `entities.id`.
- `class_year`, `current_company`, `current_title`.
- `past_companies` JSON list.
- `education` JSON list.
- `bio_summary` text.
- `last_parsed_at` nullable timestamp.
- `discovered_via` source identifier.
- Unique constraint: `(name, class_year)`.

### `entity_consolidated`

Reconciled view materialized as a table by `scripts/reconcile_entities.py`.

- `entity_id` UUID primary key and foreign key to `entities.id`.
- `name` canonical display name.
- `current_employer`, `current_title`, `class_year`, `location`.
- `source_ids` JSON object mapping each consolidated field to source row ids.
- `updated_at` timezone-aware timestamp.

## Operations Tables

### `crawl_state`

Crawler queue/status metadata.

- `id` integer primary key.
- `name`, `class_year`.
- `depth` crawl depth.
- `status` pending/running-style status text.
- `discovered_via` source identifier.

### `extraction_cache`

Chunk-level cache for triage and full extraction responses.

- `chunk_sha256` text primary-key component.
- `prompt_version` text primary-key component.
- `model` text primary-key component.
- `response_json` JSON response payload.
- `created_at` timezone-aware timestamp.

### `llm_usage`

Cost ledger. Every LLM or embedding call should write one row.

- `id` integer primary key.
- `ts` timezone-aware timestamp.
- `model` model name.
- `prompt_tokens`, `completion_tokens`.
- `dollars` computed from `backend/pricing.py`.
- `purpose` short call purpose such as `chunk_extract`, `triage`,
  `query_expansion`, `research_answer`, or `entity_name_embedding`.
- `raw_page_id` nullable source page foreign key.
- `entity_id` nullable entity foreign key.

### `audit_runs`

Extraction quality comparison runs.

- `id` integer primary key.
- `run_at` timezone-aware timestamp.
- `sample_size` requested sample size.
- `thrifty_results` JSON results from cascade mode.
- `frontier_results` JSON results from frontier-only mode.
- `diff_summary` JSON summary with per-page counts and Jaccard scores.

### `audit_events`

Append-only HTTP audit log.

- `id` integer primary key.
- `actor` admin or anonymous actor label.
- `action` audited route/action.
- `payload` redacted request payload.
- `created_at` timezone-aware timestamp.

## Relationship Types

Explicit parser edges use the model-provided `relationship_type`, defaulting to
`associate`. Current inferred relationship types are:

- `co_worked_on:<project>` - both entities have validated project rows for the
  same normalized project name. Confidence is the lower project confidence.
- `co_worked_at:<company>` - both entities have validated position facts for the
  same normalized company and overlapping employment windows. Confidence is the
  lower position confidence.
- `classmate:T'YY` - both entities consolidate to the same Tuck class year.
  Confidence is currently `0.8`.

All inferred edges set `is_inferred=true`, include a natural-language
`derivation`, and carry `source_ids` pointing back to the rows that informed the
rule.

## Provenance Rules

Every graph claim must be source-linked:

- Raw-page extractions use `source_raw_page_id` and should include
  `text_evidence`.
- Entity attributes use `source`, optional `source_url`, and confidence.
- Inferred relationships use `source_ids`, `derivation`, and confidence.
- Wikidata rows use `source='wikidata:<qid>'`, a Wikidata URL, and
  `last_verified_at`.
