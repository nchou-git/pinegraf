## Cloud Run Cold Starts

The `pinegraf` Cloud Run service is deployed with `--min-instances=1`.
Keeping one instance warm avoids the 5-15 second cold-start path on the first
admin page load after idle periods.

This costs a small fixed amount each month, but the Sources page is an admin
workflow where perceived latency matters. Request timing middleware logs
`total_ms`, `db_query_count`, and `db_time_ms` for `/api/sources`,
`/api/claims`, and `/api/claims/raw-data` so warm request latency can be
separated from database/query work.

Current deploy knobs live in `cloudbuild.yaml` and `infra/gcp/provision.sh`.
