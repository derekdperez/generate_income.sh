# TODO Improvements

## 2026-04-24

- Add a small enqueue audit trail (`enqueue_attempt_id`, `created_by`, `created_via_endpoint`, `created_at_utc`) for `coordinator_stage_tasks` so post-enqueue deletions can be attributed quickly without log forensics.
- Add an admin diagnostics endpoint that returns recent `workflow.task.reset` / `workflow.task.control` events alongside current `coordinator_stage_tasks` counts for a workflow/domain scope.
