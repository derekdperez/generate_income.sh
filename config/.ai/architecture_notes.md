# Architecture Notes

## Current Design Decisions
- Single-file implementation (`nightmare.py`) for fast iteration.
- Two-stage AI pipeline:
  1. Suggest additional candidate URLs.
  2. Produce final site analysis and cleaned sitemap narrative.
- Root-domain scope enforcement applied to discovered and AI-suggested URLs.
- Crawl resiliency uses layered control: Scrapy AutoThrottle + custom adaptive per-slot delay increases on rate-limit signals (HTTP 429/503).
- OpenAI requests now use retry with exponential backoff + jitter for transient API errors/rate limits.
- User-facing progress reporting is routed through a lightweight `ProgressReporter`; verbose phase updates are emitted from orchestration code, while crawl-page detail comes from the spider when `--verbose` is enabled.
- Discovery is broader than recursive crawling: the spider now records routes from anchor `href`, form `action`, non-anchor `href`, `src`, and embedded quoted URLs in page text, but only enqueues likely non-static targets for additional crawling.
- Discovery coverage includes escaped JS paths and generic HTML attribute values; this helps capture framework-injected routes that are not plain anchor links.
- AI integration is now optional at runtime: `--no-ai` bypasses both URL suggestion and final analysis calls so crawl-only test runs do not require API credentials.
- OpenAI request construction now includes context budgeting: crawl artifacts are compacted into sampled sitemap/url payloads and retried with progressively smaller prompt budgets if context limits are hit.
- AI orchestration is now non-fatal by design: suggestion/analysis/report stages are individually guarded, and report generation can fall back to the built-in HTML renderer when AI output fails.
- OpenAI transport now has explicit per-request timeout control to reduce unresponsive long-running API calls.
- Scrapy process startup now opts out of interactive signal-handler installation and telnet console extension to keep terminal behavior deterministic in PowerShell.
- Process startup now keeps line-buffered/writethrough stdout/stderr configuration so verbose status lines remain visible promptly in PowerShell.
- Effective runtime settings are assembled from `config/nightmare.json` plus CLI overrides, then output paths are normalized to `output/` for artifact writes.
- Crawl session persistence is now first-class: state is serialized to a session file that stores discovered/visited URLs, link graph, URL inventory, and resume frontier.
- Resume mode (`--resume`) reloads that session state and seeds the spider from the saved frontier instead of restarting from scratch.
- Resume seeding now respects remaining crawl budget at spider startup; if `visited >= max_pages`, no additional seed requests are scheduled.
- Throttling now applies across both Scrapy crawling and direct URL verification probes; verification uses an explicit request throttle interval.
- URL verification probes run as an explicit optional phase controlled by `verify_urls`; default execution skips this phase to reduce unnecessary network traffic.
- Logging now has two explicit channels: app orchestration logs and Scrapy engine logs, each with independent file paths and levels, plus configurable console level.
- Default artifact placement is domain-scoped: if no explicit path is set, output files and directories are generated under `output/<root-domain>/` with domain-prefixed names.
- Sitemap shaping now applies query-schema normalization: URLs that differ only by query values collapse to one canonical URL with typed placeholders in argument values.
- A second sitemap representation now exists for summarization: a condensed, host-scoped route tree generated from the full sitemap and optimized for minimal size while preserving route coverage semantics.
- HTML reporting is a dedicated post-processing phase: the run can emit a standard local HTML report or an AI-generated HTML report, both saved as artifacts and optionally auto-opened in the default browser.
- Textual AI analysis output now has a dedicated HTML rendering path, allowing non-`--html-report` runs to still produce a styled, browser-friendly report artifact.
- AI interaction now supports a multi-step feedback loop: schema-informed request planning -> live probe execution -> probe result summaries re-injected into final AI analysis/report generation.
- AI probe execution now includes explicit budget controls (total + per-host) and separate pacing configuration, decoupling probe behavior from URL verification settings.
- Main runtime now tracks interrupt stage and continues through artifact/report finalization after Ctrl+C, improving resiliency for long-running sessions.
- Standard HTML reporting now binds canonical sitemap URLs to representative live URLs plus local evidence file URIs so rendered report items can include both live-site links and `(file)` artifact links.
- URL provenance is now explicit: each URL is tracked with discovery source (`internal_link`, `guessed_url`, `seed_input`, `crawl_response`) and links to external evidence artifacts.
- Raw request/response evidence is persisted as compressed per-URL JSON (`.json.gz`) with compact encoding and size-capped bodies.
- Crawl interrupt handling is now application-owned instead of relying on Scrapy/Twisted-installed signal handlers: Ctrl+C bridges to an immediate reactor crash, then the outer `main()` finalization path writes the partial artifacts and exits `130`.
- After interrupted finalization completes, the CLI now flushes logging/stdio and force-exits the process. This avoids hangs during interpreter shutdown when third-party runtime cleanup would otherwise keep the process alive after the summary is printed.
- Crawl progress reporting now has two tiers: verbose mode keeps the detailed per-page/per-stage messages, while non-verbose mode emits compact summary lines during the crawl based on elapsed time or new discovery count thresholds.
- AI payload shaping now applies an explicit asset filter before prompt compaction/schema generation so image and stylesheet URLs are excluded from model context even if they still exist in crawl artifacts.
- Windows console handling now uses a narrower Quick Edit workaround: Quick Edit is disabled only while the Scrapy crawl reactor is active, then the original console mode is restored immediately after crawl shutdown/finalization.
- AI payload metadata now exposes `truncated_for_ai` as a runtime setting instead of hardcoding it. The flag is threaded through compact sitemap/schema payload generation and can be controlled by config or CLI.
- Static extension filtering is now config-backed: crawl asset exclusions and AI asset exclusions are loaded from `config/nightmare.json` at runtime instead of living as hardcoded lists in the module.
- Windows Quick Edit mode is no longer disabled automatically at startup; that mitigation prevented text selection/copy during stuck runs and made interrupted sessions feel frozen.

## Boundaries
- Crawling logic: `DomainSpider` + `crawl_domain`.
- Sitemap shaping: `build_sitemap`.
- URL inventory shaping: `build_url_inventory`.
- AI integration: `ask_openai_for_additional_urls`, `ask_openai_for_site_analysis`.

## Technical Debt / Hotspots
- Prompt budgeting is currently character-based and heuristic; token-accurate budgeting per model would be more precise.
- Backoff behavior is tuned by static defaults; may need per-target profiles for very strict sites.
- Crawl request timeout/retry behavior is still fixed in `DomainSpider.custom_settings`; it may need config/CLI control because a single blocked first request can still consume noticeable time before the user interrupts.
- User-facing string resources are now split by purpose under `config/`: CLI help text in `nightmare.help.json`, reusable message labels in `nightmare.messages.json`, AI prompt bodies in `nightmare.prompts.json`, and HTML wrappers in `config/templates/*.html`. `nightmare.py` loads and token-renders these resources at runtime instead of embedding the full text inline.
- Interrupt finalization now distinguishes essential artifact persistence from optional presentation work: once `interrupted=True`, the run still writes core JSON/text artifacts but skips HTML report generation and browser launch so it can reach the forced exit path quickly.
- The runtime no longer changes Windows console mode at all. Console freeze/Enter-to-resume behavior should be treated as a separate interrupt/runtime bug rather than handled through Quick Edit toggling.
- AI payload compaction now collapses redundant hashed bundle URLs before prompt construction. The compaction layer summarizes repeated framework/static bundle variants (for example hashed chunk files) into a single representative route so the first AI URL-suggestion pass spends context on route shape instead of build-artifact enumeration.
- Post-crawl AI execution is now allowed after a crawl-stage interrupt: if Ctrl+C stops only the spidering portion, downstream AI suggestion/analysis/report stages can still run against the recovered partial crawl state. Later-stage interrupts still stop subsequent AI work.
- Crawl request handling now treats blocked/error seed responses as first-class crawl outcomes instead of silent no-ops. Crawl requests are scheduled with `handle_httpstatus_all=True`, blocked/error responses are recorded in inventory/evidence, and request failures use a dedicated failure-evidence path.
- The spider now sends browser-like request headers (`USER_AGENT` + common browser request headers) by default to improve first-page access on sites that reject Scrapy's default client profile.
- HTML report output now uses a stable per-domain filename (`output/<root-domain>/report.html`) rather than a domain-prefixed file name. This keeps report links predictable while preserving domain-scoped output directories.
- HTML-report-producing runs now treat the report artifact as the primary presentation surface. Console output remains a compact summary plus report path/link, while the full AI analysis stays in the saved HTML file.
- The final AI reporting layer is prompt-driven. Deeper analysis requirements such as endpoint prioritization, follow-up methodology, wordlist derivation, and fuzzing guidance are now specified in the external prompt templates instead of being hardcoded in orchestration logic.
- Final AI analysis and dedicated AI HTML-report generation now share the same preservation rule: raw AI HTML should be saved as-is, with local wrapper rendering used only as a fallback for non-HTML output.
- A new per-domain source-of-truth layer now sits beside the existing sitemap/inventory outputs. It is designed as a cumulative artifact (`.jsonn`) that merges discoveries across runs and captures higher-level recon data needed for downstream testing: filtered URLs, parameter schemas, fuzz wordlists, and API endpoint testing hypotheses.
- The `.jsonn` layer is now intentionally narrower than the AI-report layer: it stores stable recon structure (routes, parameters, methods, types) and leaves generated fuzz value expansion to follow-on tooling.
- Crawl interrupt handling on Windows is now dual-layered: a native console control handler sets the interrupt event immediately on Ctrl+C / Ctrl+Break, and the existing monitor thread still performs the reactor crash/finalization handoff. This separates control-event detection from Python's delayed signal dispatch semantics on Windows consoles.
- Redirect handling now contributes to crawl discovery: 3xx responses can add an in-domain `redirect_location` edge and schedule the redirect target, which prevents apex-to-`www` canonicalization from collapsing into a one-URL crawl.
- Session identity is now root-domain-based rather than exact-start-URL-based for resume purposes, which allows existing domain sessions to survive canonical-host changes discovered across runs.
- The per-domain recon artifact layer now consists of three coordinated outputs: a merged source-of-truth JSON, a structured parameter inventory JSON, and a placeholder-oriented parameters TXT file for downstream fuzzing request generation. The TXT file is derived from the merged structural data rather than from ad hoc URL parsing at write time.
- The crawl interrupt bridge is back to pure Python/Twisted coordination only. No Windows-specific console API hooks remain in the runtime.
- Windows startup now includes non-interactive stdin rebinding (`sys.stdin` -> `os.devnull`) in addition to existing signal/reactor setup. This isolates crawl execution from console input buffer behavior and prevents hidden stdin reads from gating progress.
- `fozzy.py` is a standalone post-crawl/fuzz helper that consumes `<domain>.parameters.json` and writes its own artifacts under `output/<domain>/fozzy-output/<domain>/`.
- Fozzy outputs are purpose-split: placeholder URL permutations, concrete baseline permutations, route inventory JSON, run summary JSON, and per-request anomaly JSON files.
- `fozzy.py --dry-run` is now a first-class request planner: it emits JSONL command lines (`.fozzy.requests.jsonl`) that mirror live-run request sequencing, including requiredness probes before fuzzing permutations.
- Fozzy parameter fuzzing is now list-driven rather than type-driven: a shared quick fuzz list file controls per-parameter mutation values, with a hard expectation of 50 entries.
- Fozzy execution now exposes per-group request cardinality before fuzzing starts (`estimated_requests`) to prevent perceived hangs on very large parameter/permutation sets.
- Interrupt handling is now explicit in the group-processing loop so partial outputs are always persisted when users stop runs mid-request.
- Fozzy now emits a rollup anomaly report in the anomalies folder every run, even when count is zero, so downstream tooling can consume a stable summary file path.
- Fozzy anomaly reporting now has dual artifacts per run: machine-readable JSON summary and human-readable HTML summary generated from the same cumulative folder scan.
- Fozzy anomaly HTML report now includes interactive client-side analysis controls (sort/filter/search) without external dependencies; the report is self-contained.
- Fozzy execution policy is now full-parameter-first: for each host/path, only max-parameter request shapes are sent during fuzzing; subset permutations are no longer executed.
- Fozzy result processing now supports two finding classes: transport/response discrepancies (`anomaly`) and reflected-input findings (`reflection`) with a shared summary/report pipeline.
- Fozzy now follows a two-phase workflow: deterministic request-plan generation first, then optional live execution gated by explicit user confirmation.
- Fozzy planning stage now emits a deterministic endpoint-volume summary derived from the generated request plan, enabling quick scope/risk review before execution.
- Fozzy execution planning is now triage-capable: users can trim the live execution set by endpoint request volume after plan generation, before final execution confirmation.
- Fozzy runtime now emits user-visible liveness signals in non-verbose mode during live HTTP execution to prevent perceived hangs between interactive confirmation and first response.
