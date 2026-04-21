from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import quote

from server import DashboardHandler, _enrich_worker_snapshot_with_live_details


def _make_handler(*, coordinator_token: str, headers: dict[str, str]) -> DashboardHandler:
    handler = DashboardHandler.__new__(DashboardHandler)
    handler.server = SimpleNamespace(coordinator_token=coordinator_token)
    handler.headers = headers
    return handler


def test_is_coordinator_authorized_allows_when_token_is_not_configured():
    handler = _make_handler(coordinator_token="", headers={})
    assert handler._is_coordinator_authorized()


def test_is_coordinator_authorized_accepts_bearer_header():
    handler = _make_handler(
        coordinator_token="secret-token",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert handler._is_coordinator_authorized()


def test_is_coordinator_authorized_accepts_x_coordinator_header():
    handler = _make_handler(
        coordinator_token="secret-token",
        headers={"X-Coordinator-Token": "secret-token"},
    )
    assert handler._is_coordinator_authorized()


def test_is_coordinator_authorized_accepts_cookie_token():
    handler = _make_handler(
        coordinator_token="secret-token",
        headers={"Cookie": "foo=bar; nightmare_coord_token=secret-token; baz=qux"},
    )
    assert handler._is_coordinator_authorized()


def test_is_coordinator_authorized_accepts_url_encoded_cookie_token():
    token = "secret token + value"
    handler = _make_handler(
        coordinator_token=token,
        headers={"Cookie": f"nightmare_coord_token={quote(token, safe='')}"},
    )
    assert handler._is_coordinator_authorized()


def test_is_coordinator_authorized_rejects_incorrect_cookie_token():
    handler = _make_handler(
        coordinator_token="secret-token",
        headers={"Cookie": "nightmare_coord_token=wrong-token"},
    )
    assert not handler._is_coordinator_authorized()


def test_enrich_worker_snapshot_with_live_details_merges_log_data():
    class FakeLogStore:
        def latest_events_by_source_ids(self, source_ids):
            assert source_ids == ["worker-1"]
            return {
                "worker-1": {
                    "event_time_utc": "2026-04-20T12:34:56+00:00",
                    "description": "worker loop tick",
                    "raw_line": "worker loop tick raw",
                }
            }

    snapshot = {
        "workers": [
            {
                "worker_id": "worker-1",
                "last_action_performed": "state running",
                "last_event_emitted_at_utc": "2026-04-20T12:00:00+00:00",
                "last_heartbeat_at_utc": "2026-04-20T12:10:00+00:00",
                "last_run_time_at_utc": "",
            }
        ]
    }

    enriched = _enrich_worker_snapshot_with_live_details(snapshot, log_store=FakeLogStore())
    worker = enriched["workers"][0]
    assert worker["last_log_message"] == "worker loop tick"
    assert worker["last_log_message_at_utc"] == "2026-04-20T12:34:56+00:00"
    assert worker["last_run_time_at_utc"] == "2026-04-20T12:34:56+00:00"
