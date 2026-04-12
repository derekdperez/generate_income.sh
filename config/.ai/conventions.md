# Conventions

## Naming and Structure
- Snake_case functions and variables.
- Dataclass used for mutable crawl state (`CrawlState`).
- Helper functions for normalization/domain checks are separate from spider class.

## Todo Improvements
When implementing a request, if there is an item on the Todo Improvements list, and it would make sense to include that improvement with the current change, do so.

If an item has been sitting in the Todo Improvements for more than 5 other tasks, implement it along with the next task.

## Error Handling
- Fail fast on invalid URL and missing `OPENAI_API_KEY` when AI features are enabled.
- JSON parsing from model output is defensive with fallback extraction.
- External calls (crawl target and OpenAI API) should treat rate limits/transient failures as retryable with bounded backoff.
- Fatal top-level exceptions should always be logged with stack traces via the application logger before process exit.

## Crawl Behavior
- Default crawl policy is conservative: low base delay, per-domain concurrency cap, AutoThrottle enabled.
- If HTTP 429/503 is observed, crawl delay is increased for that downloader slot; successful responses gradually recover speed.
- Treat URL discovery and crawl-queue decisions separately: broad route extraction is allowed, but static-asset-like paths should stay out of the recursive crawl queue.
- In verbose mode, log per-page discovery source counts (raw and allowed) to make extraction/filtering failures observable.
- `--verbose` should force live crawl updates to console by elevating Scrapy log level to `INFO` unless the user explicitly overrides `--scrapy-log-level`.
- Spider progress updates in verbose mode should use direct flushed console prints in addition to logger output so updates remain visible even when Scrapy handlers are redirected.
- Resume-aware crawl scheduling must honor the remaining crawl budget (`max_pages - visited_count`) before queuing seeds, especially when resuming large sessions with a small new `--max-pages`.
- Verbose crawl output should include each newly discovered URL with its discovery source and parent page.
- Crawl throttling settings are mandatory and must remain > 0 (`crawl_delay`, `max_delay >= crawl_delay`).
- URL verification HTTP probes must also be throttled via `verify_delay` (non-zero) instead of firing back-to-back requests.
- URL existence verification is opt-in and should only run when `verify_urls` / `--verify-urls` is enabled.

## Open Questions

- Check for answers to open questions in this file after each task is complete, and complete the items that have been answered before exiting.

## Output and Artifacts
- Sitemap persisted to JSON file (`--sitemap-output`, default `sitemap.json`).
- Condensed sitemap persisted to JSON file (`--condensed-sitemap-output`, default `<domain>_sitemap_condensed.json`) as a compact single tree of distinct routes.
- URL inventory persisted to JSON file (`--url-inventory-output`, default `url_inventory.json`) with existence confirmation fields and discovery provenance.
- Every URL inventory entry should include at least one external evidence file path under `--evidence-dir`.
- Final AI analysis printed to stdout.
- Detailed runtime status should be opt-in via `--verbose`; use a small centralized reporter instead of scattering unconditional prints through crawl/OpenAI/verification code.
- Even without `--verbose`, long crawl phases should still emit compact user-facing progress summaries at a coarse cadence (time-based and/or count-based), especially for URL discovery growth.
- Verbose status lines from orchestration code should flush immediately (`flush=True`) so PowerShell sessions do not appear stalled.
- Non-AI test runs should use `--no-ai` to skip all OpenAI requests while still producing crawl artifacts and URL verification output.
- Relative artifact paths are resolved under `output/` by default; absolute paths are allowed only when explicitly provided.
- Runtime configuration should come from JSON files under `config/` (default `config/nightmare.json`) with CLI flags overriding config values.
- Session-state should be persisted to `output/` (`session_state_output`) so crawl runs can resume from saved visited/discovered/frontier data.
- Logging is split and configurable:
- application/runtime logs via `log_file` + `log_level`
- scrapy crawler logs via `scrapy_log_file` + `scrapy_log_level`
- console log verbosity via `console_log_level`
- OpenAI request timeout must be explicitly bounded (`openai_timeout` / `--openai-timeout`) to prevent long blocking API calls.
- Keep Scrapy runtime non-interactive in this CLI tool (`TELNETCONSOLE_ENABLED=False`, no Twisted-installed signal handlers) and let the application own Ctrl+C bridging/finalization behavior.
- Once an interrupted run has written its partial artifacts and printed the shutdown summary, terminate the process directly (`130`) instead of depending on interpreter shutdown to clean up third-party threads.
- On Windows, preserve normal Quick Edit behavior outside the crawl, but disable it for the active crawl window/reactor lifetime to avoid the common "crawl resumes only after Enter" selection-pause failure mode.
- When output paths are not explicitly provided, artifacts must default to `output/<root-domain>/` and each file name must include the root domain prefix.
- Sitemap output should deduplicate URLs by path + query argument schema: query values are replaced with inferred type tokens (for example `__int__`, `__bool__`, `__datetime__`) before writing.
- Condensed sitemap should prioritize compactness by compressing single-child path chains and collapsing dynamic-looking path segments into typed placeholders.
- `--html-report` should always emit a saved HTML file; in `--no-ai` mode use the built-in renderer, otherwise request an AI-authored HTML document, then print its file path + file URI.
- If AI returns plain-text/markdown instead of HTML, convert it to a styled HTML document before saving/opening so end users always get a readable report artifact.
- AI request planning must use canonical endpoint-schema input and return explicit safe request specs; execution should allow only `GET`/`HEAD`, remain in-domain, and apply throttling/timeouts.
- AI-guided probe execution must enforce both global and per-host budgets before network calls (`ai_probe_max_requests`, `ai_probe_per_host_max`) and use dedicated probe pacing (`ai_probe_delay`).
- In the standard (non-AI) HTML report, sitemap/tree items, unique URLs, and API endpoints should render as clickable live-site links; when evidence exists for an entry, append a `(file)` link to the local artifact source.
- Any OpenAI-bound payload built from sitemap/inventory artifacts must be context-budgeted (sample/truncate) before request submission and retry with smaller payloads if `context_length_exceeded` occurs.
- OpenAI-bound URL payloads should exclude static image and stylesheet asset URLs; keep those in crawl artifacts if discovered, but do not spend model context on them.
- AI compact-payload metadata flags such as `truncated_for_ai` should be runtime-configurable through the standard config/CLI merge path rather than hardcoded inside payload builders.
- File-extension classification rules used for crawl filtering or AI filtering should live in `config/nightmare.json`, not as hardcoded module-level lists.
- AI-assisted post-processing steps must be fail-safe: if a model request still cannot fit or otherwise fails, continue the run and emit artifacts using non-AI fallbacks rather than terminating the process.
- Evidence files should be persisted in compressed form and avoid storing full large response bodies; store truncated payloads plus metadata/hashes to preserve forensic utility with minimal disk usage.

## Folder Structure
- Every project should have the following folders in the root project directory:
	- output - root folder for all project-generated output
	- config - root folder for all configuration-related files
	- temp - root folder for any temporary files that will be periodically removed
- If these folders do not exist, create them. DO NOT output any project artifacts anywhere but output, do not create any configuration fiels anywhere but config, and do not put any other files in the root directory - use the temp folder for temp work
- Human-facing text that is likely to need tuning should live outside `nightmare.py`: CLI help strings in `config/nightmare.help.json`, reusable UI/message labels in `config/nightmare.messages.json`, AI prompt bodies in `config/nightmare.prompts.json`, and generated HTML skeletons in `config/templates/`. The script should reference them through the shared string-resource loader and `{{TOKEN_NAME}}` placeholders.
- Plain user-facing status lines emitted through `ProgressReporter.status()` should print directly once and must not also be forwarded through the application logger, because the logger already owns a console handler and would duplicate the line.
- Any uncaught `KeyboardInterrupt` in this CLI should terminate through the shared hard-exit path rather than `SystemExit`, because third-party runtime threads have already proven unreliable during normal interpreter shutdown.
- After an interrupted run, skip optional report rendering/browser launch work and prioritize artifact persistence plus process termination.
- Do not manipulate Windows Quick Edit or console input mode in this CLI. That workaround has proven brittle and should stay removed unless a future fix is demonstrated to solve the root cause without affecting terminal behavior.
- AI-bound URL lists and endpoint-schema inputs should be structurally condensed before prompt construction. Repeated hashed asset/bundle variants should collapse to a single summarized route rather than consuming prompt budget as individual URLs.
- `Ctrl+C` during the crawl should not automatically disable later AI stages. If the interrupt happened at `crawl`, downstream AI work may continue on the saved partial state; only interrupts during later stages should suppress the remaining AI pipeline.
- Crawl requests should be constructed through the shared spider helper so they consistently attach the errback and `handle_httpstatus_all=True` metadata.
- Seed or internal crawl failures must be recorded explicitly in URL inventory/evidence (`crawl_status_code`, `crawl_note`, failure evidence) rather than silently disappearing and leaving a misleading 0-page crawl.
- Default spider request profile should mimic a mainstream browser enough to avoid trivial first-page blocks (browser-like `USER_AGENT` and request headers).
- Default artifact naming is domain-prefixed under `output/<root-domain>/` except for the HTML report, which now defaults to the stable filename `report.html` inside the domain directory.
- If a run produces an HTML report artifact, the CLI should surface the saved path/link and rely on the browser/report file rather than echoing the full rendered analysis content back to the console.
- Prompt-owned reporting requirements should live in `config/nightmare.prompts.json`. When report structure changes but runtime data flow does not, prefer updating the prompt templates rather than adding new Python logic.
- Final AI reports should provide operational follow-up guidance, not just summaries: endpoint candidates, per-endpoint testing approach, candidate-word generation strategy, and fuzzing strategy should be explicit sections when requested.
- If an AI reporting stage already returns full HTML, save that HTML directly. Only use the text-to-HTML wrapper when the model output is genuinely plain text/markdown.
- Per-domain source-of-truth output should be persisted as a `.jsonn` file even though the contents remain standard JSON.
- When updating the `.jsonn` artifact across runs, merge by semantic key rather than overwriting blindly: unique URLs by URL string, parameterized URL rows by `url`, and API endpoint rows by `endpoint`.
- Source-of-truth URL filtering should exclude obvious static/document assets but still allow potentially useful endpoint-like resources such as `.json` URLs.
- The per-domain `.jsonn` file is a source-of-truth structural artifact, not a generated payload pack. Do not persist derived parameter wordlists there when those values are expected to be generated by a downstream tool.
- On Windows, crawl-time Ctrl+C handling should use a native console control handler as the primary signal source, with Python signal handlers only as fallback. This avoids delayed interrupt delivery while the reactor is blocking in the main thread.
- Treat in-domain redirect targets as first-class discoveries during crawling. A 3xx seed or internal response should register its `Location` URL in the link graph/inventory and enqueue it when crawlable.
- Resume validation should be strict across different root domains, but tolerant of start-URL variants within the same root domain (for example apex vs `www`).
- The source-of-truth artifact is now standard JSON, not `.jsonn`. Maintain backward compatibility by reading legacy `.jsonn` if present, but write only `.json` going forward.
- Parameter extraction outputs are split by purpose:
- `<domain>_source_of_truth.json` for merged structural recon data
- `<domain>.parameters.json` for parameter/value inventory
- `<domain>.parameters.txt` for placeholder-built request strings used by downstream fuzzing tools
- Placeholder request strings should assign unique identifiers by type and ordinal within each URL, for example `{int1}`, `{int2}`, `{string1}`, `{url1}`.
- Do not use Windows console-mode or native console-control API workarounds in this CLI. Quick Edit toggling and `SetConsoleCtrlHandler`-based fixes are both removed.
- Keep this CLI non-interactive at runtime on Windows: stdin should be redirected to `os.devnull` to prevent hidden dependency reads from stalling crawl/output loops until keyboard input is provided.
- Parameter-fuzz tooling should stay separate from crawl orchestration: `fozzy.py` consumes the emitted parameter inventory instead of importing runtime internals from `nightmare.py`.
- Fuzz anomaly artifacts must include a replayable request identifier (`requested_url`) plus baseline/anomaly response summaries for quick triage.
- For fuzz tooling, dry-run output should be line-oriented JSON (`.jsonl`) with exactly one record per planned HTTP request and a reproducible `curl` command field.
- In `fozzy.py`, baseline parameter values should come from canonical type defaults for deterministic request generation; observed parameter values should not drive baseline mutation seeds.
- Quick fuzz list cardinality is now data-driven. `fozzy.py` should use all values present in the configured quick list file instead of enforcing a fixed size.
- Long-running fuzz loops should emit periodic request-progress lines and include preflight request estimates to keep CLI behavior observable.
- CLI tools that make network loops should catch `KeyboardInterrupt` at orchestration boundaries and save partial artifacts before exiting.
- Fuzz/anomaly pipelines should always write a deterministic summary artifact after each run (including zero-anomaly runs) to support automation and quick triage.
- Anomaly summaries should be cumulative across runs by scanning the anomaly artifact directory, not only in-memory results from the current execution.
- Human-facing run reports should be emitted as HTML tables for rapid review while preserving JSON for automation.
- Human-facing HTML reports should keep dense data readable by truncating long fields in-table while preserving full values via tooltips/title attributes.
- For triage reports, include interactive sort/filter/search directly in artifact HTML so review does not depend on external tooling.
- To control request volume, fozzy should execute only max-parameter permutations for each route and avoid subset permutation fuzzing by default.
- New fozzy runs should persist findings under `results/`; when migrating existing domains, include legacy `anomalies/` artifacts in summary rollups to avoid losing historical context.
- For potentially high-volume fuzz runs, generate and persist the full request plan before network execution and require an affirmative user confirmation in non-dry mode.
- Any high-volume request plan should include endpoint-level request counts sorted descending and persisted as standalone artifacts for pre-execution review.
- For interactive live fuzz runs, request-plan review should include an endpoint-level request-cap filter step prior to final proceed/cancel confirmation.
- Long-running live request loops should print heartbeat/progress in non-verbose mode, including a message before the first network call in each group.
- Fozzy HTML report cells should preserve full underlying text for copy/paste and search (`data-raw` + full text content) while using CSS clipping for compact display; avoid irreversible string truncation in report generation.
- Report tables should expose direct clickable links for local artifact files using `file:///` hrefs when file paths are presented.

