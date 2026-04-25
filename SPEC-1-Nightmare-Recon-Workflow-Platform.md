# SPEC-1-Nightmare Recon Workflow Platform

## Background

Nightmare is a Python-based, coordinator-driven security reconnaissance and workflow orchestration platform. The uploaded application contains roughly 134 Python files and about 61k lines of Python, organized around:

- a web/API control plane (`server.py`, `server_app/fastapi_app.py`),
- PostgreSQL-backed task, artifact, workflow, and worker state stores (`server_app/store.py`, `workflow_app/store.py`),
- distributed coordinator workers (`coordinator.py`, `coordinator_app/runtime.py`),
- executable plugins (`plugins/*`),
- recon workflows (`workflows/*.workflow.json`),
- domain crawling, parameter inventory, fuzzing, extractor, and auth-differential modules (`nightmare.py`, `fozzy.py`, `extractor.py`, `auth0r/*`).

The platform appears designed for authorized reconnaissance of configured target domains. Its core value is repeatable, resumable multi-stage discovery: enumerate subdomains, crawl applications, infer parameters/endpoints, fuzz requests, extract high-value findings, and present workflow results through an operator UI.

Similar tools or systems by purpose:

- OWASP ZAP: interactive and automated web application security testing.
- Burp Suite: proxy-driven web testing with scanner and extension ecosystem.
- Nuclei: template-based vulnerability scanning workflows.
- Amass/Subfinder: subdomain and asset discovery.
- Scrapy-based crawlers: programmable crawling and extraction.

Nightmare differs by combining crawler output, generated artifacts, parameter-aware fuzzing, plugin scheduling, workflow UI, and distributed worker coordination in one local/private orchestration stack.

## Requirements

### Must Have

- Support authorized target-domain registration and recon execution.
- Persist targets, stage tasks, artifacts, workflow definitions, workflow runs, worker presence, and recent events in PostgreSQL.
- Provide a web/API control plane for operators to enqueue, monitor, pause, run, reset, and inspect workflows.
- Execute workflow stages through distributed workers that claim tasks, heartbeat leases, emit progress, and complete or fail stages.
- Support file-backed built-in workflows and DB-backed workflow definitions.
- Provide plugin-based execution with explicit input artifacts, output artifacts, config schema, retry behavior, and preconditions.
- Support resumable recon stages:
  - subdomain enumeration,
  - optional subdomain takeover checks,
  - crawler stages,
  - high-value extraction,
  - parameter-aware fuzzing,
  - authentication/differential analysis where configured.
- Store artifacts in both metadata and object/manifest tables so reports and downstream stages can consume prior outputs.
- Provide contractor-implementable deployment using Docker Compose, PostgreSQL, central server, and one or more worker containers.
- Enforce scope controls so execution is limited to explicitly registered/authorized domains.

### Should Have

- Add claim diagnostics for queued/ready/blocked/running tasks and plugin allowlist visibility.
- Normalize all task-generation paths so Recon Control and Workflow Builder use the same backend initiation service.
- Provide a worker health dashboard showing idle/running workers, ready tasks, blocked tasks, leases, and recent claim failures.
- Add structured audit logs for workflow definition changes and manual task controls.
- Add resource leasing and concurrency caps per plugin/resource class.
- Add first-class retention classes for artifacts: ephemeral, derived rebuildable, evidence, report, and checkpoint.
- Expose OpenAPI for the FastAPI coordinator API.
- Separate operator UI server from worker-facing coordinator API for cleaner deployment and auth boundaries.

### Could Have

- Multi-tenant organization/project model.
- SSO/RBAC for operators.
- Scheduled recurring scans with cron/interval workflow schedules.
- Object storage backend for large artifacts.
- Graph view of discovered assets, URLs, parameters, forms, scripts, and findings.
- Plugin SDK with test harness and manifest validation.
- Queue metrics export to Prometheus/Grafana.

### Won't Have in MVP

- Unscoped internet-wide scanning.
- Exploit automation against third-party systems.
- Fully managed SaaS multi-tenancy.
- A custom queue service; PostgreSQL remains the MVP queue source of truth.
- A complete replacement for established DAST scanners.

## Method

### Current Application Shape

The application is already close to a modular distributed recon platform. The recommended architecture is to preserve the current core model while tightening task lifecycle, diagnostics, schema boundaries, and implementation seams.

```plantuml
@startuml
skinparam componentStyle rectangle

actor Operator

rectangle "Central Control Plane" {
  component "server.py\nOperator UI + APIs" as Server
  component "server_app.fastapi_app\nWorker Coordinator API" as FastAPI
  component "Workflow Builder" as Builder
  component "Reporting Pages" as Reporting
}

rectangle "PostgreSQL" {
  database "coordinator_* tables" as CoordDB
  database "workflow_* tables" as WorkflowDB
}

rectangle "Workers" {
  component "coordinator.py" as Coordinator
  component "Plugin Runtime" as Runtime
  component "Recon Plugins" as ReconPlugins
  component "Nightmare Crawler" as Nightmare
  component "Fozzy Fuzzer" as Fozzy
  component "Extractor" as Extractor
  component "Auth0r" as Auth0r
}

rectangle "Artifacts" {
  component "Artifact metadata" as ArtMeta
  component "Artifact objects/manifest" as ArtObj
}

Operator --> Server
Server --> Builder
Server --> Reporting
Server --> CoordDB
Server --> WorkflowDB
FastAPI --> CoordDB
FastAPI --> WorkflowDB
Coordinator --> FastAPI : claim/heartbeat/progress/complete
Coordinator --> Runtime
Runtime --> ReconPlugins
ReconPlugins --> Nightmare
ReconPlugins --> Fozzy
ReconPlugins --> Extractor
ReconPlugins --> Auth0r
Runtime --> ArtMeta
Runtime --> ArtObj
ArtMeta --> CoordDB
ArtObj --> CoordDB
@enduml
```

### Runtime Model

1. Operator registers one or more authorized root domains.
2. Operator starts a workflow from Recon Control or Workflow Builder.
3. Server creates stage tasks in `coordinator_stage_tasks` and/or workflow run records in `workflow_runs` and `workflow_step_runs`.
4. Workers poll `/api/coord/stage/claim-next`.
5. Claim logic picks ready tasks based on workflow preconditions, plugin availability, resource leases, and force-run flags.
6. Worker runs the matching plugin.
7. Plugin writes progress, checkpoints, and artifacts.
8. Completion updates the task, refreshes dependent task readiness, and emits recent events.
9. UI reads rollups, artifacts, stage tasks, and worker presence for status/results pages.

```plantuml
@startuml
actor Operator
participant "Control Plane" as UI
participant "Coordinator Store" as Store
database "PostgreSQL" as DB
participant "Worker" as Worker
participant "Plugin" as Plugin

Operator -> UI : Start recon workflow
UI -> Store : enqueue stages(root_domain, workflow_id)
Store -> DB : insert coordinator_stage_tasks
Store -> DB : insert recent event

loop worker polling
  Worker -> UI : POST /api/coord/stage/claim-next
  UI -> Store : try_claim_stage_with_resources()
  Store -> DB : select ready task + acquire lease
  UI --> Worker : claimed task
end

Worker -> Plugin : run(context)
Plugin -> UI : progress/checkpoint/artifact
UI -> Store : persist progress/artifacts
Store -> DB : update task/artifact rows
Plugin --> Worker : result code + message
Worker -> UI : complete stage
UI -> Store : mark completed/failed
Store -> DB : update task, refresh readiness
@enduml
```

### Core Domains

#### Target

A root domain or host explicitly registered for authorized recon. Stored in `coordinator_targets`.

Recommended fields:

```sql
CREATE TABLE coordinator_targets (
  root_domain TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'pending',
  scope_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### Stage Task

A single executable workflow stage for one target.

Current source of truth: `coordinator_stage_tasks`.

Recommended MVP contract:

```sql
CREATE TABLE coordinator_stage_tasks (
  id BIGSERIAL PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  root_domain TEXT NOT NULL,
  stage TEXT NOT NULL,
  plugin_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  priority INTEGER NOT NULL DEFAULT 100,
  preconditions_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  checkpoint_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  progress_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 1,
  paused BOOLEAN NOT NULL DEFAULT FALSE,
  force_run_override BOOLEAN NOT NULL DEFAULT FALSE,
  worker_id TEXT,
  lease_expires_at TIMESTAMPTZ,
  started_at_utc TIMESTAMPTZ,
  completed_at_utc TIMESTAMPTZ,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(workflow_id, root_domain, stage)
);

CREATE INDEX idx_stage_claim
  ON coordinator_stage_tasks(status, paused, lease_expires_at, priority, created_at_utc);

CREATE INDEX idx_stage_domain_workflow
  ON coordinator_stage_tasks(root_domain, workflow_id, status);
```

Valid statuses:

- `pending`: created but waiting for preconditions.
- `ready`: claimable.
- `running`: leased by a worker.
- `completed`: terminal success.
- `failed`: terminal failure after retries or non-retryable error.
- `blocked`: cannot proceed until manual fix.
- `paused`: manually held.

#### Artifact

A persisted output from a plugin. The application already has `coordinator_artifacts`, `coordinator_artifact_objects`, and `coordinator_artifact_manifest_entries`.

Recommended artifact fields:

```sql
CREATE TABLE coordinator_artifacts (
  id BIGSERIAL PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  root_domain TEXT NOT NULL,
  stage TEXT NOT NULL,
  artifact_type TEXT NOT NULL,
  logical_role TEXT NOT NULL DEFAULT '',
  media_type TEXT NOT NULL DEFAULT 'application/json',
  retention_class TEXT NOT NULL DEFAULT 'derived_rebuildable',
  payload_json JSONB,
  object_ref TEXT NOT NULL DEFAULT '',
  sha256 TEXT NOT NULL DEFAULT '',
  size_bytes BIGINT NOT NULL DEFAULT 0,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_artifacts_lookup
  ON coordinator_artifacts(workflow_id, root_domain, artifact_type, created_at_utc DESC);
```

#### Plugin Definition

Executable plugin metadata exists in runtime registry and DB-backed workflow builder tables. The MVP should keep runtime execution in Python but persist plugin metadata for UI authoring.

Key plugins discovered in the application:

- `auth0r`
- `extractor`
- `fozzy`
- `recon_subdomain_enumeration`
- `recon_subdomain_takeover`
- `recon_spider_source_tags`
- `recon_spider_script_links`
- `recon_spider_wordlist`
- `recon_spider_ai`
- `recon_extractor_high_value`
- dynamic `nightmare_*` artifact gate plugins

Recommended plugin interface:

```python
class CoordinatorPlugin:
    plugin_name: str

    def contract(self) -> StageContract:
        ...

    def prepare_inputs(self, context: PluginExecutionContext) -> dict:
        ...

    def run(self, context: PluginExecutionContext) -> tuple[int, str]:
        ...

    def emit_outputs(self, context: PluginExecutionContext, outputs: dict) -> None:
        ...

    def flush_checkpoint(self, context: PluginExecutionContext, checkpoint: dict) -> None:
        ...
```

### Built-In Recon Workflow

The built-in workflow `run-recon` should be modeled as a DAG, not only a linear list.

```plantuml
@startuml
skinparam componentStyle rectangle

component "recon_subdomain_enumeration" as Subdomains
component "recon_subdomain_takeover" as Takeover
component "recon_spider_source_tags" as SourceTags
component "recon_spider_script_links" as ScriptLinks
component "recon_spider_wordlist" as Wordlist
component "recon_spider_ai" as AI
component "recon_extractor_high_value" as Extractor
component "fozzy" as Fozzy
component "auth0r" as Auth0r

Subdomains --> Takeover : recon_subdomains_json
Subdomains --> SourceTags : recon_subdomains_json
SourceTags --> ScriptLinks : nightmare_session_json
ScriptLinks --> Wordlist : nightmare_session_json
Wordlist --> AI : nightmare_session_json
SourceTags --> Extractor : collected scripts/responses
ScriptLinks --> Extractor
Wordlist --> Extractor
AI --> Extractor
SourceTags --> Fozzy : parameter inventory
ScriptLinks --> Fozzy
Wordlist --> Fozzy
AI --> Fozzy
Auth0r --> Fozzy : authenticated sessions, optional
@enduml
```

### Task Claim Algorithm

The current system already has claim-next and readiness refresh logic. The MVP should make this deterministic and auditable.

```python
def claim_next(worker_id, allowed_plugins, now):
    expire_stale_running_tasks(now)
    refresh_stage_task_readiness()

    candidates = select_ready_tasks(
        status='ready',
        paused=False,
        plugin_name_in=allowed_plugins,
        lease_expired_or_empty=True,
        order_by=['priority ASC', 'created_at_utc ASC'],
    )

    for task in candidates:
        if not task.force_run_override:
            if target_already_running(task.root_domain, task.workflow_id):
                continue
            if not preconditions_satisfied(task):
                mark_pending_or_blocked(task)
                continue

        if acquire_resource_lease(task):
            update task set
              status='running',
              worker_id=worker_id,
              lease_expires_at=now + lease_duration,
              attempts=attempts + 1,
              force_run_override=False
            emit_event('workflow.task.claimed')
            return task

    return None
```

Precondition checks should support:

- all required artifacts exist,
- any required artifact exists,
- all required plugin stages completed,
- target status is in allowed statuses,
- manual force-run override bypass for a single claim only.

### Worker Heartbeat and Lease Recovery

Each running task must have a lease expiry. Workers heartbeat before expiry. If a worker dies:

1. server sees `lease_expires_at < now`,
2. task returns to `ready` if attempts remain,
3. task becomes `failed` if attempts are exhausted,
4. event log receives `workflow.task.lease_expired`,
5. artifacts emitted before failure remain available but are not treated as completion flags unless declared complete.

### API Surface

The existing FastAPI coordinator API should be the stable worker-facing API.

MVP endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Liveness probe |
| `GET /api/coord/database-status` | DB health |
| `POST /api/coord/register-targets` | Register scoped targets |
| `POST /api/coord/stage/enqueue` | Enqueue one or more stage tasks |
| `POST /api/coord/stage/claim-next` | Claim next ready stage |
| `POST /api/coord/stage/heartbeat` | Extend task lease |
| `POST /api/coord/stage/progress` | Save checkpoint/progress |
| `POST /api/coord/stage/complete` | Complete or fail task |
| `POST /api/coord/stage/control` | Pause/run/delete/reset task |
| `POST /api/coord/artifact` | Persist JSON/small artifact |
| `POST /api/coord/artifact/stream` | Persist large artifact stream |
| `GET /api/coord/artifacts` | List artifacts |
| `GET /api/coord/workflow-snapshot` | UI workflow snapshot |
| `GET /api/coord/workflow-domain` | Per-target workflow status |
| `POST /api/coord/workflow/run` | Start workflow run |

### Operator UI

The existing templates should be retained and simplified around these pages:

- Targets
- Workers
- Workflows
- Workflow Definitions
- Recon Control
- Recon Results
- Artifacts
- Database Status
- Docker Status
- Logs
- Claim Diagnostics

The missing diagnostic page should show:

```json
{
  "workflow_id": "run-recon",
  "root_domain": "example.com",
  "counts_by_status": {
    "pending": 3,
    "ready": 2,
    "running": 1,
    "completed": 5,
    "failed": 0,
    "blocked": 1
  },
  "claim_candidates": 2,
  "blocked_reasons": [
    {
      "stage": "recon_spider_script_links",
      "reason": "missing artifact nightmare_session_json"
    }
  ],
  "workers": {
    "idle": 4,
    "running": 1,
    "stale": 0
  },
  "plugin_allowlists": {
    "worker-1": ["recon_subdomain_enumeration", "recon_spider_source_tags"]
  }
}
```

### Configuration

Configuration currently exists under `config/*.json`, `deploy/.env*`, Dockerfiles, and Compose files. The MVP should standardize around:

```env
DATABASE_URL=postgresql://nightmare:nightmare@postgres:5432/nightmare
COORDINATOR_API_TOKEN=change-me
SERVER_BIND_HOST=0.0.0.0
SERVER_PORT=8000
WORKER_ID=worker-001
WORKER_PLUGIN_ALLOWLIST=recon_subdomain_enumeration,recon_spider_source_tags,recon_spider_script_links
ARTIFACT_RETENTION_DAYS=30
DEFAULT_STAGE_LEASE_SECONDS=300
OPENAI_API_KEY=
```

### Security and Scope Controls

The product must assume authorized testing only.

Required MVP controls:

- Require target registration before enqueueing tasks.
- Normalize and validate root domains using a consistent parser.
- Block private/internal CIDR targets unless explicitly enabled for internal deployments.
- Apply per-target request throttle defaults.
- Store operator action audit events.
- Require a coordinator API token for worker-facing APIs.
- Avoid storing plaintext credentials; Auth0r profiles should use encryption helpers already present in `auth0r/crypto.py` and `auth0r/profile_store.py`.
- Keep generated reports free of secrets by default; add redaction for headers, cookies, bearer tokens, and form values.

### Deployment Architecture

```plantuml
@startuml
node "Docker Host / VM" {
  database "PostgreSQL" as PG
  node "central" {
    component "Nightmare Server" as S
  }
  node "worker-1" {
    component "Coordinator Worker" as W1
  }
  node "worker-2" {
    component "Coordinator Worker" as W2
  }
  folder "shared artifact volume" as Vol
}

S --> PG
W1 --> S : coordinator API
W2 --> S : coordinator API
S --> Vol
W1 --> Vol
W2 --> Vol
@enduml
```

MVP deployment should use:

- Python 3.12+ container image,
- PostgreSQL 16+,
- Docker Compose for central + workers,
- bind-mounted or named Docker volume for large artifacts,
- environment-specific `.env`,
- health checks for server and database,
- restart policy for workers.

## Implementation

### Phase 1: Stabilize the Existing Application

1. Remove committed virtual environments, caches, local output, certificates, and generated artifacts from distributable packages.
2. Add a repository hygiene check that fails if `.venv*`, `__pycache__`, `.pytest_cache`, local TLS keys, output logs, or generated zip files are included.
3. Confirm `requirements.txt` installs cleanly in a fresh Python 3.12 container.
4. Run the existing test suite in CI.
5. Split `server.py` into modules:
   - `server_app/routes/ui.py`,
   - `server_app/routes/coordinator.py`,
   - `server_app/routes/workflows.py`,
   - `server_app/routes/artifacts.py`,
   - `server_app/routes/diagnostics.py`.
6. Keep `server.py` as a thin entrypoint.

### Phase 2: Normalize Workflow Launch

1. Create one backend service function:

```python
def start_workflow_run(
    workflow_id: str,
    root_domains: list[str],
    selected_stages: list[str] | None,
    parameter_overrides: dict,
    actor: str,
) -> WorkflowLaunchResult:
    ...
```

2. Call this service from both Recon Control and Workflow Builder.
3. Persist a `workflow_runs` record for every launch.
4. Generate `workflow_step_runs` and `coordinator_stage_tasks` in one database transaction.
5. Emit a launch event with request payload hash and generated task IDs.

### Phase 3: Claim Diagnostics

1. Add store method:

```python
def get_claim_diagnostics(workflow_id: str | None, root_domain: str | None) -> dict:
    ...
```

2. Return counts by status, claim candidates, blocked reasons, plugin allowlists, stale leases, and recent lifecycle events.
3. Add API endpoint:

```http
GET /api/coord/claim-diagnostics?workflow_id=run-recon&root_domain=example.com
```

4. Add UI page `templates/claim_diagnostics.html.j2`.
5. Add tests covering:
   - ready task with no worker,
   - ready task hidden by plugin allowlist,
   - blocked missing artifact,
   - stale lease recovery,
   - force-run override.

### Phase 4: Harden Plugin Contracts

1. Require each plugin to expose `StageContract` with:
   - input artifact declarations,
   - output artifact declarations,
   - checkpoint schema,
   - resource class,
   - concurrency group,
   - max parallelism.
2. Validate workflow JSON at startup.
3. Fail fast if a workflow references a missing plugin.
4. Add plugin contract snapshot tests.
5. Add a plugin SDK guide for contractors.

### Phase 5: Artifact Store Improvements

1. Add artifact retention classes.
2. Store large outputs in `coordinator_artifact_objects` or a filesystem/object-store backend.
3. Add SHA-256 and size metadata.
4. Add artifact lookup helper:

```python
def latest_artifact(root_domain, workflow_id, artifact_type) -> Artifact | None:
    ...
```

5. Add manifest entries for report-visible files.
6. Add artifact cleanup job by retention class.

### Phase 6: Production Readiness

1. Add API token auth to all worker-facing coordinator endpoints.
2. Add optional operator login for UI.
3. Add structured logs with request IDs and worker IDs.
4. Add `/metrics` endpoint or log-based metrics for:
   - task counts by status,
   - claim latency,
   - task duration,
   - failure rate by plugin,
   - artifact write failures,
   - stale lease count.
5. Add backup/restore docs for PostgreSQL and artifacts.

## Milestones

### Milestone 1: Clean Build and Baseline Tests

- Fresh Docker build succeeds.
- Existing tests run in CI.
- Package excludes caches, virtualenvs, local keys, generated outputs.
- Server starts and health endpoint passes.

### Milestone 2: Unified Workflow Launch

- Recon Control and Workflow Builder both use the same launch service.
- A launch creates workflow run, step runs, stage tasks, and lifecycle events in one transaction.
- Duplicate launch behavior is deterministic and documented.

### Milestone 3: Reliable Distributed Claiming

- Workers claim ready tasks consistently.
- Stale leases are recovered.
- Force-run override works once and then clears.
- Resource leases enforce plugin concurrency.
- Claim diagnostics identify why a task is not running.

### Milestone 4: Plugin Contract Completeness

- Every built-in plugin has a contract.
- Workflow JSON validates at startup.
- Missing plugin or missing artifact dependency is detected before execution.
- Contract tests are added for all built-in plugins.

### Milestone 5: Artifact and Results UX

- Artifacts are searchable by domain, workflow, stage, and type.
- Recon Results page displays subdomains, crawl progress, discovered files, endpoint inventory, extractor findings, and fuzz anomalies.
- Large artifact streaming works without loading whole files into memory.

### Milestone 6: Secure MVP Deployment

- Central and worker Compose deployment works from `.env`.
- Coordinator API token is required.
- Registered target scope is enforced.
- Logs and metrics support operational debugging.
- Backup/restore process is documented.

## Gathering Results

### Functional Acceptance

- Register `example.com` as an authorized test target.
- Start `run-recon` from Recon Control.
- Verify stage tasks are created for selected plugins.
- Verify at least one worker claims the first ready task.
- Verify progress and checkpoint updates are visible in UI.
- Verify downstream stages become ready only after required artifacts or plugin completions exist.
- Verify final results page shows generated artifacts and summaries.

### Reliability Metrics

- Task claim success rate: target > 99% for ready tasks with available workers.
- Stale lease recovery: target < 2 lease durations.
- Task lifecycle event coverage: target 100% for enqueue, claim, progress, complete, fail, reset, pause, run, delete.
- Worker heartbeat visibility: target every active worker seen within one heartbeat interval.
- Workflow launch consistency: Recon Control and Workflow Builder generate equivalent task graphs for equivalent inputs.

### Performance Metrics

- Claim-next endpoint p95 latency under 250 ms with 10k stage tasks.
- Workflow snapshot endpoint p95 latency under 1 second for 1k domains.
- Artifact metadata lookup p95 latency under 200 ms with correct indexes.
- Large artifact upload should stream without holding the entire artifact in process memory.

### Security Validation

- Unauthorized domains cannot be enqueued.
- Worker APIs reject missing or invalid API token.
- Sensitive request headers and cookies are redacted in reports by default.
- Auth profiles are encrypted at rest or omitted from reports.
- Manual task control events include actor, target, workflow, stage, action, and timestamp.

### Contractor Handoff Checklist

- Database schema migration scripts.
- Docker Compose for central and worker roles.
- `.env.example` with all required variables.
- Plugin contract documentation.
- API examples for target registration, workflow launch, claim, heartbeat, progress, complete, and artifact upload.
- Integration tests for a minimal single-target workflow.
- Runbook for diagnosing idle workers with queued tasks.

## Need Professional Help in Developing Your Architecture?

Please contact me at [sammuti.com](https://sammuti.com) :)
