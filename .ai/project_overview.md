# Project Overview

- Purpose: Python security tooling for parameter extraction/fuzzing workflows.
- Key scripts observed: `nightmare.py` (main scanner/orchestrator), `fozzy.py` (parameter permutation + fuzz runner), `extractor.py` (supporting extraction flow).
- Runtime model: CLI-driven scripts, filesystem-based inputs/outputs under repo-local folders like `output/`, `temp/`, `resources/`, and `config/`.
- Distributed runtime model: coordinator target queue (`coordinator_targets`) handles domain intake, while workflow/plugin stage tasks (`coordinator_stage_tasks`) are scheduled from workflow config and executed by plugin workers.
- Reporting runtime model: coordinator web APIs now include a server-side page cache with periodic warming for default dashboard pages (crawl progress, discovered targets, discovered files/high-value files) to improve first-paint responsiveness.
- Major dependencies observed in `fozzy.py`: standard library (`argparse`, `threading`, `concurrent.futures`, `urllib`, `pathlib`) plus optional `tldextract`.
- Recon workflow profile added: `workflows/run-recon.workflow.json` with pluginized subdomain enumeration, spider-method stages, and high-value extraction.
