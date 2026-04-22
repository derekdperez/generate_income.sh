# Domain Glossary

- `parameters.json`: Per-domain input inventory used to derive fuzz permutations.
- `RouteGroup`: Grouping key for fuzz execution by `(host, path)`.
- `incremental state`: Per-domain folder snapshot metadata used to decide whether a domain should be re-run.
- `master results summary`: Aggregated JSON/HTML report across domain output trees.
- `workflow_id`: Logical orchestration namespace for plugin tasks; allows multiple workflow definitions to coexist without key collisions.
- `plugin task`: A resumable unit in `coordinator_stage_tasks` keyed by `(workflow_id, root_domain, stage)`.
- `checkpoint_json`: Worker-owned exact-resume state for a plugin task.
- `progress_json`: Operator-facing live execution state for events/UI while a plugin task runs.
