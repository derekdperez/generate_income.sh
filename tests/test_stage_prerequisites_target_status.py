from coordinator import DistributedCoordinator
from server_app.store import CoordinatorStore


class _FakeCursor:
    def __init__(self) -> None:
        self._last_query = ""

    def execute(self, query: str, params=None) -> None:
        self._last_query = " ".join(str(query or "").split()).lower()

    def fetchall(self):
        if "select artifact_type from coordinator_artifacts" in self._last_query:
            return []
        if "select stage, status from coordinator_stage_tasks" in self._last_query:
            return []
        return []

    def fetchone(self):
        if "count(*) filter" in self._last_query and "from coordinator_targets" in self._last_query:
            return (0, 0, 0, 0)
        return None


def test_scheduler_prerequisite_check_treats_missing_targets_as_pending():
    entry = {"prerequisites": {"target_statuses": ["pending", "running", "completed"]}}
    assert DistributedCoordinator._has_stage_prerequisites(
        set(),
        entry,
        workflow_tasks={},
        target_counts={},
    )


def test_store_prerequisite_check_treats_missing_targets_as_pending():
    store = CoordinatorStore.__new__(CoordinatorStore)
    store._load_workflow_stage_preconditions = lambda *_args, **_kwargs: {  # type: ignore[attr-defined]
        "target_statuses": ["pending", "running", "completed"]
    }
    ready, reason = CoordinatorStore._stage_prerequisites_satisfied(
        store,
        _FakeCursor(),
        workflow_id="run-recon",
        root_domain="example.com",
        stage="recon_subdomain_enumeration",
    )
    assert ready
    assert reason == ""


def test_store_prerequisite_check_still_honors_require_completed_target():
    store = CoordinatorStore.__new__(CoordinatorStore)
    store._load_workflow_stage_preconditions = lambda *_args, **_kwargs: {  # type: ignore[attr-defined]
        "require_target_completed": True
    }
    ready, reason = CoordinatorStore._stage_prerequisites_satisfied(
        store,
        _FakeCursor(),
        workflow_id="run-recon",
        root_domain="example.com",
        stage="recon_subdomain_enumeration",
    )
    assert not ready
    assert "waiting for completed target" in reason
