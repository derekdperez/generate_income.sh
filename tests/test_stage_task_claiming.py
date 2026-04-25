from __future__ import annotations

from typing import Any

from server_app.store import CoordinatorStore


class _RecordingCursor:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, query: str, params: Any = None) -> None:
        self.queries.append(" ".join(str(query or "").split()).lower())

    def fetchall(self) -> list[Any]:
        return []

    def fetchone(self) -> Any:
        return None


def test_stage_resource_lease_cleanup_removes_orphaned_active_leases() -> None:
    store = object.__new__(CoordinatorStore)
    cur = _RecordingCursor()

    store._cleanup_stage_resource_leases_cur(cur)

    joined = "\n".join(cur.queries)
    assert "delete from coordinator_resource_leases where lease_expires_at < now()" in joined
    assert "delete from coordinator_resource_leases l" in joined
    assert "not exists" in joined
    assert "from coordinator_stage_tasks s" in joined
    assert "s.status = 'running'" in joined
    assert "s.worker_id = l.worker_id" in joined
    assert "s.lease_expires_at >= now()" in joined
