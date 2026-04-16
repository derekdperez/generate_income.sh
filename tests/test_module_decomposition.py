from pathlib import Path

from coordinator_app.runtime import load_config
from server_app.store import CoordinatorStore


def test_new_module_files_exist():
    assert Path("server_app/store.py").is_file()
    assert Path("coordinator_app/runtime.py").is_file()


def test_coordinator_runtime_exports():
    assert callable(load_config)
    assert CoordinatorStore.__name__ == "CoordinatorStore"


def test_server_uses_external_coordinator_store():
    server_source = Path("server.py").read_text(encoding="utf-8")
    assert "from server_app.store import CoordinatorStore" in server_source
    assert "class CoordinatorStore" not in server_source
    assert "from reporting.server_pages import render_dashboard_html, render_workers_html" in server_source
    assert "def _render_dashboard_html" not in server_source
    assert "def _render_workers_html" not in server_source


def test_store_includes_database_status_method():
    assert callable(getattr(CoordinatorStore, "database_status", None))
