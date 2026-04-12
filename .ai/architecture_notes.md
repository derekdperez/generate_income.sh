# Architecture Notes

- `fozzy.py` has two primary entry paths:
  - Single-domain run via explicit `<domain>.parameters.json` argument.
  - Incremental multi-domain run when no parameters file is passed (`run_incremental_domains`).
- Incremental change detection currently depends on scanning each domain folder and comparing a max mtime snapshot with `.fozzy_incremental_state.json`.
- Startup responsiveness in incremental mode is user-critical; no-output periods are interpreted as hangs.
- Technical hotspot: deep recursive filesystem scans over large `output` trees can dominate startup time.
- Batch orchestrator state writes (`output/batch_state/batch_run_state.json`) can hit transient Windows file-lock conflicts during atomic rename.
- `_atomic_write_json` should tolerate short-lived `PermissionError` windows with bounded retry/backoff.
