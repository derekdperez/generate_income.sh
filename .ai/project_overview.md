# Project Overview

- Purpose: Python security tooling for parameter extraction/fuzzing workflows.
- Key scripts observed: `nightmare.py` (main scanner/orchestrator), `fozzy.py` (parameter permutation + fuzz runner), `extractor.py` (supporting extraction flow).
- Runtime model: CLI-driven scripts, filesystem-based inputs/outputs under repo-local folders like `output/`, `temp/`, `resources/`, and `config/`.
- Major dependencies observed in `fozzy.py`: standard library (`argparse`, `threading`, `concurrent.futures`, `urllib`, `pathlib`) plus optional `tldextract`.
