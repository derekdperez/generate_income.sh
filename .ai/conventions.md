# Conventions

- CLI scripts print explicit human-readable status lines and output artifact paths.
- Config defaults are defined in code and merged with JSON config files (`config/fozzy.json`), then optionally overridden by CLI flags.
- Incremental state is persisted as JSON in scan roots (example: `.fozzy_incremental_state.json`).
- For performance-sensitive recursion in large trees, prefer lower-overhead directory iteration (`os.scandir`) over heavy `Path.rglob` usage.
- For long-running batch orchestration, state persistence failures should be warning-level and non-fatal when possible, so active work is not aborted by transient file locks.
- Extractor reporting rows may include `importance_score` (int) and should preserve it end-to-end: wordlist -> extractor summary rows -> master report table.
- For report table columns that represent numeric risk/priority metrics, mark header `data-type="number"` and store raw numeric value in `data-raw` to keep client-side sort numeric.
