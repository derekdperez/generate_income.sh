# Performance and processing speed

This document describes what drives runtime for the Nightmare/Fozzy tooling, how the optional **HTTP request queue** fits in, and how to tune for throughput versus safety. It does **not** include fixed benchmark numbers: throughput depends on target sites, network latency, crawl limits, and hardware.

## Has the HTTP queue hurt performance?

**In short: a little per request when enabled, but it is usually not the dominant cost.**

- **Nightmare’s main crawl does not use this queue.** The spider uses Scrapy. Crawl speed is governed by Scrapy settings, `crawl_delay`, `max_pages`, the path wordlist, and the remote site—not by `HttpRequestQueue`.
- The **queue** (`http_request_queue.py`, toggled by `http_queue_enabled` in `config/nightmare.json` and `config/fozzy.json`) is used for **out-of-band HTTP** from Python:
  - **Fozzy:** every fuzz / probe request when the queue is enabled (see `perform_fozzy_http_request` in `fozzy.py`).
  - **Nightmare:** AI probe requests and **`probe_url_existence`** (the optional `--verify-urls` / `verify_urls` stage), not the Scrapy crawl loop.

**What the queue adds when it is on**

- Each logical request is **written to SQLite** (WAL mode, `synchronous=NORMAL`), may be **leased** and **executed** in `submit_and_wait`, and results are **stored** again. That is extra disk I/O and locking versus calling `request_capped` directly.
- `submit_and_wait` **polls** until the job completes: if the worker cannot immediately claim work, it sleeps in **50 ms** steps (`time.sleep(0.05)`), which can add small latency under contention.
- **Retries** on retryable HTTP failures (e.g. 5xx, 429) use backoff (`http_queue_retry_base_seconds`, `http_queue_retry_max_seconds`, up to `http_queue_max_attempts`), which improves reliability but **extends** wall time for failing or flaky endpoints.

**What stays the same**

- The actual HTTP work still uses the shared **httpx** client and **`request_capped`** (`http_client.py`) inside `execute_claimed`. Network time and server response time dominate for typical runs.

**When to disable the queue for maximum raw HTTP speed**

Set `"http_queue_enabled": false` in the relevant config (`fozzy.json` for Fozzy, `nightmare.json` for Nightmare’s queued call sites). You lose durable queue semantics and the queue’s retry behavior for those code paths; requests go straight to `request_capped`.

---

## Nightmare (`nightmare.py`)

| Factor | Effect |
|--------|--------|
| **`max_pages`** | Hard cap on visited pages; primary limiter of crawl size. |
| **`crawl_delay`** | Throttle between Scrapy requests; larger values slow the crawl. |
| **Path wordlist** (`crawl_wordlist`) | Hundreds of extra seeds from `resources/wordlists/file_path_list.txt` (unless changed); adds requests and inventory work. |
| **`verify_urls` / `verify_delay` / `verify_timeout`** | Optional second pass that HEAD/GETs every inventoried URL; can dominate runtime when enabled. Uses the HTTP queue when `http_queue_enabled` is true. |
| **AI stages** | Model calls (`model`, `openai_timeout`) and probes (`ai_probe_max_requests`, `ai_probe_delay`, `ai_probe_per_host_max`) add latency bounded by APIs, not local CPU. |
| **`batch_workers`** | Parallel domain processes in batch modes; higher values increase parallelism until disk/CPU/network saturate. |

**Takeaway:** Crawl throughput is **Scrapy + delays + max_pages**. The SQLite HTTP queue affects **probes and verification**, not the main spider’s request pipeline.

---

## Fozzy (`fozzy.py`)

| Factor | Effect |
|--------|--------|
| **`delay_seconds`** | Sleep between planned HTTP operations in live runs; often the largest intentional slowdown (politeness / rate limiting). |
| **`timeout_seconds`** | Upper bound wait per request. |
| **Permutation count × quick fuzz list** | Determines how many baseline + mutation requests are planned. |
| **`max_background_workers`**, **`max_workers_per_domain`**, **`max_workers_per_subdomain`** | Parallelism across groups; `max_workers_per_subdomain` is often 1 to avoid hammering a single host. |
| **`http_queue_enabled`** | When true, each HTTP goes through `HttpRequestQueue.submit_and_wait` (persistence + possible polling/retry overhead). |

**Takeaway:** Fozzy runtime scales with **request count × (delay + typical HTTP latency)**. The queue adds **overhead on top of** that, but disabling it is the lever for minimal per-request overhead if you accept non-queued behavior.

---

## Coordinator / extractor / server

These components are largely **network- and subprocess-bound** (claiming work, uploading artifacts, running Nightmare/Fozzy/Extractor as child processes). Their performance follows the same rules as the underlying tools above, plus API and storage latency.

---

## Practical tuning (quick reference)

1. **Crawl faster (Nightmare):** reduce `crawl_delay`, lower `max_pages` only if you want less work (not faster per page), trim or disable a large `crawl_wordlist`, leave `verify_urls` off unless needed.
2. **Fuzz faster (Fozzy):** reduce `delay_seconds`, cap permutations / `max_requests_per_endpoint`, raise worker caps cautiously; set `http_queue_enabled` to `false` if you need the least overhead per HTTP call.
3. **Queue vs direct HTTP:** `http_queue_enabled: true` (default in repo configs) trades a bit of speed for **durability, retries, and a single serialized pipeline** to the DB for those call sites; set to `false` for maximum throughput on local benchmarking.

---

## Observability

- Nightmare can log **dev timing** when `dev_timing_logging` is enabled in config (`nightmare.json`).
- The queue exposes a **`stats()`** helper on `HttpRequestQueue` (row counts by status) for inspecting backlog; see `http_request_queue.py`.

No repository-hosted A/B timings are maintained here; re-measure on your targets if you need hard numbers.
