import json

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


class _FailedTargetCursor(_FakeCursor):
    def fetchone(self):
        if "count(*) filter" in self._last_query and "from coordinator_targets" in self._last_query:
            return (0, 0, 0, 1)
        return None


def test_scheduler_prerequisite_check_treats_missing_targets_as_pending():
    entry = {"prerequisites": {"target_statuses": ["pending", "running", "completed"]}}
    assert DistributedCoordinator._has_stage_prerequisites(
        set(),
        entry,
        workflow_tasks={},
        target_counts={},
    )


def test_scheduler_prerequisite_check_allows_failed_target_for_recon_subdomain_enumeration():
    entry = {
        "plugin_name": "recon_subdomain_enumeration",
        "prerequisites": {"target_statuses": ["pending", "running", "completed"]},
    }
    assert DistributedCoordinator._has_stage_prerequisites(
        set(),
        entry,
        workflow_tasks={},
        target_counts={"failed": 1},
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


def test_store_prerequisite_check_allows_failed_target_for_recon_subdomain_enumeration():
    store = CoordinatorStore.__new__(CoordinatorStore)
    store._load_workflow_stage_preconditions = lambda *_args, **_kwargs: {  # type: ignore[attr-defined]
        "target_statuses": ["pending", "running", "completed"]
    }
    ready, reason = CoordinatorStore._stage_prerequisites_satisfied(
        store,
        _FailedTargetCursor(),
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


class _DbPrereqCursor:
    def execute(self, _query: str, _params=None) -> None:
        return None

    def fetchone(self):
        return ({"require_target_completed": True},)

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _DbPrereqConn:
    def cursor(self):
        return _DbPrereqCursor()

    def commit(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_store_run_recon_file_preconditions_override_stale_db(tmp_path):
    workflow_file = tmp_path / "run-recon.workflow.json"
    workflow_file.write_text(
        json.dumps(
            {
                "workflow_id": "run-recon",
                "plugins": [
                    {
                        "plugin_name": "recon_subdomain_enumeration",
                        "preconditions": {},
                        "inputs": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = CoordinatorStore.__new__(CoordinatorStore)
    store._workflow_catalog_dir = lambda: tmp_path  # type: ignore[attr-defined]
    store._connect = lambda: _DbPrereqConn()  # type: ignore[attr-defined]
    prereq = CoordinatorStore._load_workflow_stage_preconditions(
        store,
        workflow_id="run-recon",
        stage="recon_subdomain_enumeration",
    )
    assert prereq == {}
