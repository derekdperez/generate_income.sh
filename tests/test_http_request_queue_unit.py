from __future__ import annotations

import base64
import time
from pathlib import Path

import pytest

import http_request_queue
from http_client import CappedResponse
from http_request_queue import HttpRequestQueue


@pytest.fixture
def queue(tmp_path: Path) -> HttpRequestQueue:
    return HttpRequestQueue(
        tmp_path / "queue.sqlite3",
        tmp_path / "spool",
        lease_seconds=5,
        retry_base_seconds=0.01,
        retry_max_seconds=0.01,
        worker_id="test-worker",
    )


def test_enqueue_and_dedupe_returns_existing_id(queue: HttpRequestQueue):
    rid1 = queue.enqueue(method="GET", url="https://example.com/a", dedupe_key="same")
    rid2 = queue.enqueue(method="GET", url="https://example.com/b", dedupe_key="same")
    assert rid1 == rid2


def test_claim_next_respects_priority(queue: HttpRequestQueue):
    queue.enqueue(method="GET", url="https://example.com/low", priority=200)
    rid_high = queue.enqueue(method="GET", url="https://example.com/high", priority=10)
    job = queue.claim_next()
    assert job is not None
    assert job.request_id == rid_high
    assert job.attempts == 1


def test_requeue_expired_leases(queue: HttpRequestQueue):
    rid = queue.enqueue(method="GET", url="https://example.com/x")
    job = queue.claim_next()
    assert job is not None
    with queue._connect() as conn:
        conn.execute(
            "UPDATE http_request_queue SET lease_expires_at=? WHERE request_id=?",
            (time.time() - 1.0, rid),
        )
    assert queue.requeue_expired_leases() == 1
    reclaimed = queue.claim_next()
    assert reclaimed is not None
    assert reclaimed.request_id == rid
    assert reclaimed.attempts >= 2


def test_mark_success_and_load_result(queue: HttpRequestQueue):
    rid = queue.enqueue(method="GET", url="https://example.com/success")
    job = queue.claim_next()
    assert job is not None
    payload = {"ok": True, "request_id": rid}
    queue.mark_success(rid, payload, status_code=200)
    loaded = queue.load_result(rid)
    assert loaded == payload


def test_mark_retry_sets_retry_wait(queue: HttpRequestQueue, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(http_request_queue.random, "uniform", lambda a, b: 0.0)
    rid = queue.enqueue(method="GET", url="https://example.com/retry", max_attempts=3)
    job = queue.claim_next()
    assert job is not None
    queue.mark_retry(rid, {"ok": False}, 503, "HTTP 503", attempt_no=1, max_attempts=3)
    stats = queue.stats()["counts"]
    assert stats.get("retry_wait", 0) == 1


def test_mark_retry_dead_letters_when_attempt_limit_reached(queue: HttpRequestQueue):
    rid = queue.enqueue(method="GET", url="https://example.com/dead", max_attempts=1)
    job = queue.claim_next()
    assert job is not None
    queue.mark_retry(rid, {"ok": False}, 500, "boom", attempt_no=1, max_attempts=1)
    stats = queue.stats()["counts"]
    assert stats.get("dead_letter", 0) == 1


def test_execute_claimed_success_marks_succeeded(queue: HttpRequestQueue, monkeypatch: pytest.MonkeyPatch):
    rid = queue.enqueue(method="POST", url="https://example.com/ok", body=b"payload")
    job = queue.claim_next()
    assert job is not None

    def fake_request_capped(*args, **kwargs):
        return CappedResponse(
            status_code=200,
            url="https://example.com/ok",
            headers={"content-type": "text/plain"},
            body=b"done",
            elapsed_ms=12,
        )

    monkeypatch.setattr(http_request_queue, "request_capped", fake_request_capped)
    result = queue.execute_claimed(job)
    assert result["ok"] is True
    loaded = queue.load_result(rid)
    assert isinstance(loaded, dict)
    assert loaded["response"]["body_base64"] == base64.b64encode(b"done").decode("ascii")


def test_execute_claimed_http_503_marks_retry_wait(queue: HttpRequestQueue, monkeypatch: pytest.MonkeyPatch):
    rid = queue.enqueue(method="GET", url="https://example.com/503", max_attempts=2)
    job = queue.claim_next()
    assert job is not None
    monkeypatch.setattr(http_request_queue.random, "uniform", lambda a, b: 0.0)

    def fake_request_capped(*args, **kwargs):
        return CappedResponse(
            status_code=503,
            url="https://example.com/503",
            headers={},
            body=b"unavailable",
            elapsed_ms=5,
        )

    monkeypatch.setattr(http_request_queue, "request_capped", fake_request_capped)
    result = queue.execute_claimed(job)
    assert result["ok"] is False
    assert queue.stats()["counts"].get("retry_wait", 0) == 1


def test_execute_claimed_http_404_marks_dead_letter(queue: HttpRequestQueue, monkeypatch: pytest.MonkeyPatch):
    rid = queue.enqueue(method="GET", url="https://example.com/404", max_attempts=2)
    job = queue.claim_next()
    assert job is not None

    def fake_request_capped(*args, **kwargs):
        return CappedResponse(
            status_code=404,
            url="https://example.com/404",
            headers={},
            body=b"missing",
            elapsed_ms=5,
        )

    monkeypatch.setattr(http_request_queue, "request_capped", fake_request_capped)
    result = queue.execute_claimed(job)
    assert result["ok"] is False
    assert queue.stats()["counts"].get("dead_letter", 0) == 1

