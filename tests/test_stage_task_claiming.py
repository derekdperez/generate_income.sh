from __future__ import annotations

from typing import Any

from app_platform.server.store import CoordinatorStore


class _CleanupCursor:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.rowcount = 2

    def execute(self, query: str, params: Any = None) -> None:
        _ = params
        self.queries.append(" ".join(str(query or "").split()).lower())


def test_stage_claim_cleanup_removes_orphaned_resource_leases() -> None:
    cursor = _CleanupCursor()
    store = object.__new__(CoordinatorStore)

    deleted = store._cleanup_orphaned_stage_resource_leases_cur(cursor)

    assert deleted == 2
    assert cursor.queries
    cleanup_sql = cursor.queries[-1]
    assert "delete from coordinator_resource_leases" in cleanup_sql
    assert "not exists" in cleanup_sql
    assert "coordinator_stage_tasks" in cleanup_sql
    assert "s.status = 'running'" in cleanup_sql
