from __future__ import annotations

from plugins.fuzzing.fozzy_app.response_analysis import ResponseAnalysisPipeline


def _resp(
    *,
    status: int,
    body: str,
    url: str = "https://example.com/api/users?id=1",
    elapsed_ms: int = 120,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    hdr = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Strict-Transport-Security": "max-age=31536000",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=()",
    }
    if headers:
        hdr.update(headers)
    return {
        "status": status,
        "size": len(body.encode("utf-8")),
        "body_preview": body,
        "elapsed_ms": elapsed_ms,
        "response_headers": hdr,
        "url": url,
        "http_method": "GET",
    }


def _ctx(mutated_value: str = "FUZZ_TESTTOKEN_12345", url: str = "https://example.com/api/users?id=1") -> dict[str, object]:
    return {
        "request_id": "req-1",
        "http_method": "GET",
        "request_url": url,
        "host": "example.com",
        "path": "/api/users",
        "parameter_layout": ["id"],
        "mutated_parameter": "id",
        "mutated_value": mutated_value,
    }


def test_dynamic_timestamp_noise_is_suppressed():
    pipeline = ResponseAnalysisPipeline()
    base = _resp(status=200, body='{"ok":true,"ts":"2026-04-17T01:02:03Z","request_id":"abc123456"}')
    fuzz = _resp(status=200, body='{"ok":true,"ts":"2026-04-18T09:44:33Z","request_id":"zzz999999"}')
    result = pipeline.analyze_response(request_context=_ctx("1234"), baseline_response=base, fuzzed_response=fuzz)
    assert result["score"] <= 8
    assert result["status"] in {"normal", "low_signal"}


def test_status_500_with_java_stacktrace_detected():
    pipeline = ResponseAnalysisPipeline()
    base = _resp(status=200, body='{"ok":true}')
    fuzz_body = """
java.lang.NullPointerException: boom
    at org.springframework.web.DispatcherServlet.doDispatch(DispatcherServlet.java:1039)
Caused by: java.lang.IllegalStateException: invalid
"""
    fuzz = _resp(status=500, body=fuzz_body, headers={"Content-Type": "text/html; charset=utf-8"})
    result = pipeline.analyze_response(request_context=_ctx(), baseline_response=base, fuzzed_response=fuzz)
    ids = {item["id"] for item in result["findings"]}
    assert "status_change" in ids
    assert "java_stack_trace" in ids
    assert result["score"] >= 50


def test_new_debug_header_appears():
    pipeline = ResponseAnalysisPipeline()
    base = _resp(status=200, body='{"ok":true}')
    fuzz = _resp(status=200, body='{"ok":true}', headers={"X-Debug-Token": "abc"})
    result = pipeline.analyze_response(request_context=_ctx(), baseline_response=base, fuzzed_response=fuzz)
    ids = {item["id"] for item in result["findings"]}
    assert "new_headers" in ids


def test_security_header_disappears():
    pipeline = ResponseAnalysisPipeline()
    base = _resp(status=200, body='{"ok":true}')
    fuzz = _resp(
        status=200,
        body='{"ok":true}',
        headers={
            "Content-Security-Policy": "",
            "X-Frame-Options": "",
            "X-Content-Type-Options": "",
            "Strict-Transport-Security": "",
            "Referrer-Policy": "",
            "Permissions-Policy": "",
        },
    )
    # Simulate removal by explicitly omitting headers from fuzz response.
    fuzz["response_headers"] = {"Content-Type": "application/json; charset=utf-8"}
    result = pipeline.analyze_response(request_context=_ctx(), baseline_response=base, fuzzed_response=fuzz)
    ids = {item["id"] for item in result["findings"]}
    assert "missing_security_headers" in ids


def test_json_becomes_html_error_page():
    pipeline = ResponseAnalysisPipeline()
    base = _resp(status=200, body='{"ok":true,"data":{"id":1}}')
    fuzz = _resp(
        status=500,
        body="<html><title>Internal Server Error</title><h1>Error</h1></html>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    result = pipeline.analyze_response(request_context=_ctx(), baseline_response=base, fuzzed_response=fuzz)
    assert any(item["id"] == "header_semantic_change" for item in result["findings"])
    assert result["body_diff_stats"]["content_type_changed"] is True


def test_reflection_detected_in_script_context():
    pipeline = ResponseAnalysisPipeline()
    marker = "FUZZ_REFLECT_ABC123"
    base = _resp(status=200, body="<html><script>var x='safe';</script></html>", headers={"Content-Type": "text/html"})
    fuzz = _resp(
        status=200,
        body=f"<html><script>var user='{marker}';</script></html>",
        headers={"Content-Type": "text/html"},
    )
    result = pipeline.analyze_response(request_context=_ctx(marker), baseline_response=base, fuzzed_response=fuzz)
    ids = {item["id"] for item in result["findings"]}
    assert "reflection_detected" in ids
    matches = result["reflection"]
    assert matches and matches[0]["marker"] == marker


def test_redirect_to_login_detected():
    pipeline = ResponseAnalysisPipeline()
    base = _resp(status=200, body='{"ok":true}', headers={"Location": ""})
    fuzz = _resp(
        status=302,
        body="",
        headers={"Location": "https://example.com/login?next=%2Fapi%2Fusers&nonce=abc123xyz987"},
    )
    result = pipeline.analyze_response(request_context=_ctx(), baseline_response=base, fuzzed_response=fuzz)
    assert result["body_diff_stats"]["content_type_changed"] is False
    assert "redirect_changed" in result["tags"]


def test_repeated_identical_exceptions_cluster_together():
    pipeline = ResponseAnalysisPipeline()
    base = _resp(status=200, body='{"ok":true}')
    fuzz = _resp(
        status=500,
        body="java.lang.RuntimeException: bad\nat org.springframework.a.B.c(B.java:22)",
        headers={"Content-Type": "text/plain"},
    )
    a = pipeline.analyze_response(request_context=_ctx("FUZZ_A"), baseline_response=base, fuzzed_response=fuzz)
    b = pipeline.analyze_response(request_context=_ctx("FUZZ_B"), baseline_response=base, fuzzed_response=fuzz)
    assert a["cluster_id"] == b["cluster_id"]
    assert b["cluster_occurrence"] >= 2


def test_sql_error_detected():
    pipeline = ResponseAnalysisPipeline()
    base = _resp(status=200, body='{"ok":true}')
    fuzz = _resp(
        status=500,
        body="SQLSTATE[42000]: syntax error at or near 'SELECT' in PostgreSQL",
        headers={"Content-Type": "text/plain"},
    )
    result = pipeline.analyze_response(request_context=_ctx(), baseline_response=base, fuzzed_response=fuzz)
    categories = set(result["error_categories"])
    assert "sql_database_error" in categories


def test_spring_whitelabel_detected():
    pipeline = ResponseAnalysisPipeline()
    base = _resp(status=200, body="<html><title>Home</title><p>OK</p></html>", headers={"Content-Type": "text/html"})
    fuzz = _resp(
        status=500,
        body="<html><title>Whitelabel Error Page</title><h1>Whitelabel Error Page</h1></html>",
        headers={"Content-Type": "text/html"},
    )
    result = pipeline.analyze_response(request_context=_ctx(), baseline_response=base, fuzzed_response=fuzz)
    assert "server_container_error" in set(result["error_categories"])
    assert isinstance(result["findings"], list)
    # Stable output schema keys for UI sorting/filtering.
    for key in (
        "request_id",
        "baseline_id",
        "cluster_id",
        "normalized_signature",
        "status",
        "summary",
        "score",
        "findings",
        "header_diff",
        "body_diff_stats",
        "similarity",
        "reflection",
        "extracted_exceptions",
        "error_categories",
        "tags",
    ):
        assert key in result

