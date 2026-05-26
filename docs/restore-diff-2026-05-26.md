# Restore Diff - 2026-05-26

Compared current production (`pinegraf-db`) against clone
`pinegraf-db-restore-20260526-0947` using read-only transactions.

| Table | Current prod | 09:47 UTC clone |
| --- | ---: | ---: |
| sources | 0 | 0 |
| source_runs | 0 | 0 |
| fetches | 0 | 0 |
| documents | 0 | 0 |
| claims | 0 | 0 |
| entities | 0 | 0 |
| live_logs | 6 | 4 |

Current prod Alembic version: `0013_audit_log`.
Clone Alembic version: `0011_live_logs`.

No source identifiers are present in current prod but absent from the clone.
Swapping to this clone would not recover source, run, fetch, document, claim, or entity data.
It would also move the database schema backward unless migrations were applied after restore.
