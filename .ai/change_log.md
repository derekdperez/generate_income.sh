# AI Change Log

## 2026-04-24

- Navigation and page model simplified:
  removed legacy pages and kept workers/workflows/workflow-generated pages/workflow builder/database/docker status/view logs.

- Workflow monitor/task controls reworked:
  task actions are now `Delete`, `Pause`, `Run` with backend support in `/api/coord/stage/control`.

- Recon workflow interfaces expanded:
  recon control/results pages now host consolidated recon operations and output views.

- Workflow run enqueue path hardened:
  added persistence verification diagnostics and retry logic when scheduled rows are not observed immediately.

- Force-run claim path fixed:
  tasks flagged with `force_run_override` can bypass running-target gate during candidate selection.

- `.ai` memory set refreshed:
  outdated conventions/architecture notes replaced with current architecture and workflow/runtime behavior.

- Workflow task visibility/persistence hardening:
  `workflows.html` now pulls workflow snapshot with `cache_mode=refresh` (not `prefer`), `/api/workflow-runs` now seeds built-ins before run creation, and `create_workflow_run` now requires `root_domain` and raises if stage-task row persistence is zero.

- Workflow-run persistence guard tests added:
  regression tests now cover missing `root_domain`, zero persisted-stage-row failure, and persisted row count returned in the run result payload.

- Recon bootstrap readiness fix:
  updated `run-recon` so `recon_subdomain_enumeration` has no preconditions, and changed stage-precondition resolution to treat workflow-file preconditions as authoritative for `run-recon`/`recon-workflow` to avoid stale DB rules leaving first-step tasks stuck in `pending`.

- Added readiness-regression coverage:
  tests now assert built-in recon import keeps empty preconditions for subdomain enumeration and that run-recon file preconditions override stale DB preconditions.
