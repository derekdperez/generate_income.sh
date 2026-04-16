# TODO Improvements

- Consider adding a lightweight incremental fingerprint strategy that avoids full recursive scans of every domain folder on every run.
- Consider a startup summary timer (e.g., elapsed time for dirty-check stage) to aid diagnostics on large datasets.
- Consider surfacing quick-fuzz-list path validation earlier with a clearer remediation hint in CLI output.
- Continue god-file decomposition in staged slices:
  - `nightmare.py`: split CLI/config parsing, crawl orchestration, and output artifact writers into dedicated modules.
  - `fozzy.py`: split report rendering payload builders from fuzz execution control flow.
  - `extractor.py`: split matcher engine, summary aggregation, and I/O/reporting boundaries.
