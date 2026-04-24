# AI Change Log

## 2026-04-24

- Reduced primary navigation to operational pages and workflow-generated pages only.
- Removed legacy workflow monitor panels (enqueue/reset/timeline) and replaced plugin task actions with `Delete`, `Pause`, and `Run`.
- Added backend task-control support:
  - `POST /api/coord/stage/control` (server + fastapi),
  - `CoordinatorStore.control_stage_task(...)`,
  - `paused` status filter support in reset/status parsing,
  - force-run prerequisite override in claim path.
- Expanded recon results interface into consolidated output workspace:
  - recon summary table/actions,
  - crawl progress table,
  - discovered target domains + sitemap table,
  - discovered files + high-value file tables,
  - extractor domain/match table + zip download.
- Updated recon control domain API to include all discovered domains (not only snapshot domains) while retaining workflow task counters.
- Hardened workflow enqueue persistence checks in both HTTP stacks:
  - `POST /api/coord/workflow/run` now performs an immediate persistence re-check and one controlled re-enqueue retry if the first enqueue pass reports scheduled rows but the table still reads empty.
  - Response payloads now include `requeue_attempted` and `requeue_counts` so transient post-enqueue wipes are visible.
  - FastAPI error payload for zero-persist condition now returns structured diagnostics.
- Relaxed persistence verification to avoid false negatives from plugin-filter mismatches:
  - Workflow run now verifies persisted rows at `workflow_id + root_domain` scope (with plugin-filter count reported separately for diagnostics).
  - Error payload now reports both `persisted_stage_task_rows_root_workflow` and `persisted_stage_task_rows_plugin_filtered`.
