# Open Questions

## 2026-04-24

- In some environments, `/api/coord/workflow/run` reports scheduled rows but immediate post-enqueue verification can still read zero persisted rows. Need to confirm whether this is caused by an external task-reset caller, DB-level trigger/job, or environment-level DB routing mismatch.
