# Change Log

## 2026-04-12

- Updated `fozzy.py` incremental scan behavior to reduce "appears hung" startup:
  - Replaced `Path.rglob`-based mtime traversal with `os.scandir` iterative traversal in `folder_tree_max_mtime_ns` for lower overhead on large trees.
  - Added immediate per-domain progress output during incremental dirty-check pass in `run_incremental_domains`.
- Why: default no-argument execution could spend a long time in pre-run change detection with no visible output.
- Hardened `nightmare.py` batch state persistence against transient Windows file-lock failures:
  - `_atomic_write_json` now uses a unique temp filename and retries atomic replace with short backoff before failing.
  - Batch `persist()` now catches write errors and logs a warning instead of terminating the orchestrator.
- Why: observed crash on `[WinError 5] Access is denied` when rotating `batch_run_state.json` during batch completion.
- Added extractor `importance_score` support to reporting pipeline:
  - `extractor.py` now carries `importance_score` from `resources/wordlists/extractor_list.txt` into both match detail files and `extractor/summary.json` rows.
  - Master HTML report table in `fozzy.py` now includes an `Importance` numeric column with sortable header and per-column filter input.
- Why: extractor list now includes score metadata and report needs analyst triage by severity/priority.
