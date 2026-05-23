# Cleanup Report

## Static Analysis

- `ruff check --select F401,F811,F841`: clean, no unused imports,
  redefinitions, or unused local variables.
- `vulture backend/ scripts/ --min-confidence 80`: reported only
  `backend/config.py:56` unused validator argument `cls`. Renamed it to
  `_cls`; rerunning Vulture is clean.

## Removed

- `frontend/index.html.bak` and `frontend/app.js.bak`
  - Safe to remove because they were stale backup files. The active frontend is
    served from `frontend/index.html` and `frontend/app.js`; no Python, JS, HTML,
    or migration references pointed at the `.bak` files.
- `scripts/cleanup_attribution.log`
  - Safe to remove because it was generated output from
    `scripts/cleanup_attribution.py`. The script recreates this path when run;
    the log file itself should not be versioned.
- `eval_results.json`
  - Safe to remove because no repo code referenced it. It was generated eval
    output and is already ignored by `.gitignore`.
- Empty unused directories: `backend/storage`, `backend/discovery`,
  `backend/discovery/sources`, `backend/inference`, `backend/extraction`, and
  `backend/extraction/extractors`
  - Safe to remove because they contained no tracked files and no imports or
    frontend calls referenced them.

## Deliberately Left

- Public read endpoints `/profiles`, `/connections`, `/projects`, and `/facts`
  were left in place even though the current frontend does not call all of them.
  They are public API surface and may be used by scripts or external callers.
- `USE_MOCK_*` code paths were left in place. They are documented and covered by
  existing API, parser, query, audit, and pipeline tests.
- Alembic migration helpers were left untouched. Some appear isolated by design,
  but migrations need to remain self-contained and downgrade-capable.
