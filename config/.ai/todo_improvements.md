# TODO Improvements

- Add prompt-size controls (URL batching/chunked analysis) for large crawls.
- Add optional HTTP status capture and content-type classification per URL.
- Add tests for URL normalization, domain filtering, JSON extraction behavior, and broad route discovery (`href`, `src`, form actions, embedded quoted paths).
- Add retention controls to cap evidence file growth on very large crawls.
- Update Scrapy integration for deprecation warnings:
  - migrate `start_requests()` to `start()` compatibility pattern
  - update middleware method signature to avoid future `process_response` spider-arg deprecation break
## AI Prompt Budgeting
- Replace char-based OpenAI prompt budgeting with model-specific token estimation so truncation is predictable and minimizes information loss.
## Evidence Lifecycle
- Add a built-in evidence rotation/retention command (for example keep N latest runs or max total size per domain) to prevent long-term disk growth.
