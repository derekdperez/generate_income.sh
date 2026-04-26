# Workflow Run End-to-End Guide

This runbook walks you through running a workflow from the web UI and verifying that workers claim and execute tasks until completion.

## Prerequisites

Before starting, confirm:

- Coordinator server is up and reachable in browser.
- At least one plugin worker is online (not paused/stopped).
- The target domain(s) you want to run against already exist in the system.
- You know the coordinator API token (if your deployment requires auth in browser calls).

## 1) Open the Workflow UI

1. Open the web app in your browser.
2. Navigate to the workflow run page (the page where you choose workflow and click **Run Workflow**).
3. Select the workflow you want to run (for example, `run-recon`).

Expected result:

- Workflow plugins are visible.
- Run controls are enabled (including Force checkbox if present).

## 2) Choose Domains and Plugins

1. Select one or more root domains.
2. Select plugins (or leave default set).
3. If you want a forced re-run, check the **Force** checkbox.
4. Click **Run Workflow**.

Expected result:

- UI shows a success response with scheduling details.
- You should see nonzero scheduled counts for at least some plugins/domains.

## 3) Validate Scheduling Immediately

After clicking Run, check that rows were created:

1. Open Dashboard.
2. Find **Running Workflows** and **System Events** tables.
3. Confirm events like:
   - `workflow.task.created`
   - `workflow.task.ready` (or `workflow.task.pending` if prerequisites exist)

Expected result:

- At least one task appears as `ready` or `running`.

## 4) Verify Worker Claiming

In Dashboard or worker/status pages:

1. Watch plugin workers status transition from `idle` to `running`.
2. Watch events for:
   - `workflow.task.claimed`
   - `workflow.task.started`
   - `worker.heartbeat`

Expected result:

- Ready tasks are claimed within polling interval.
- Claimed tasks show assigned `worker_id`.

## 5) Watch Live Progress

While workflow is running:

1. Keep Dashboard open.
2. Watch:
   - **System Events** (real-time updates)
   - **Database Inserts / Table Activity**
   - **Running Workflows**
3. Optionally open recon/results page and observe generated assets/artifacts.

Expected result:

- Progress events appear (`workflow.task.progress`).
- Artifacts are written for completed steps.

## 6) Confirm Successful Completion

A successful run should end with:

- Task terminal events:
  - `workflow.task.completed`
  - `workflow.task.succeeded`
- No remaining `running` tasks for that workflow run.
- Final task statuses are `completed` (or expected mix if some plugins intentionally skipped).

## 7) Optional Control Actions During Run

You can test operator controls:

- **Cancel run**: use workflow runs page cancel action; verify `workflow.run.canceled` + task canceled events.
- **Retry failed tasks**: use retry-failed action and verify tasks move back to `ready`/`pending`.

## 8) Troubleshooting Checklist

If workflow appears scheduled but not progressing:

1. Confirm workers are online and not paused.
2. Confirm worker plugin allowlist includes selected plugin names (dash/underscore variants are normalized).
3. Confirm tasks are actually in `coordinator_stage_tasks` and have `ready` status.
4. Check System Events for prerequisite blocks:
   - `workflow.task.pending`
   - blocked reason contains “Waiting for Prerequisites...”
5. Verify no stale lock condition:
   - Running target lease can block non-workflow-scoped tasks by design.
6. Check for claim/start events:
   - If missing, worker claim path is failing.
7. Reload workers (if config/plugins changed), then rerun workflow.

## 9) Recommended “Demo Run” Script

Use this sequence when you want to watch a full run in real time:

1. Open Dashboard tab.
2. Open Workflow Run tab.
3. Start workflow with one domain and small plugin set.
4. Return to Dashboard and watch events + worker transitions.
5. Open results page once first success events appear.
6. Verify final completion events and no active running tasks.

---

If you want, I can also add a short “operator quickstart” section into `docs/index.html` linking to this runbook.
