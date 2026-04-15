from pathlib import Path

from coordinator_app.runtime import load_config
from server_app.store import CoordinatorStore


def test_new_module_files_exist():
    assert Path("server_app/store.py").is_file()
    assert Path("coordinator_app/runtime.py").is_file()


def test_coordinator_runtime_exports():
    assert callable(load_config)
    assert CoordinatorStore.__name__ == "CoordinatorStore"
