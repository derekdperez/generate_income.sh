#!/usr/bin/env python3
"""API catalog helpers for the in-app API Debugger."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any


HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD")

_ENDPOINT_SAMPLE_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("GET", "/api/coord/ping"): {"description": "Verify coordinator routing is alive."},
    ("GET", "/api/coord/readiness"): {"description": "Check coordinator database/schema readiness."},
    ("GET", "/api/coord/database-status"): {"description": "Inspect coordinator database status."},
    ("GET", "/api/summary"): {"description": "Load the live dashboard summary payload."},
    ("GET", "/api/log-tail"): {"query": {"limit": "200"}, "description": "Read recent server log output."},
    ("GET", "/api/coord/log-tail"): {"query": {"limit": "200", "source": ""}},
    ("GET", "/api/coord/events"): {"query": {"limit": "250", "offset": "0", "search": "", "event_type": "", "sort_dir": "desc"}},
    ("GET", "/api/coord/event-log"): {"query": {"limit": "250", "after_sequence": "0"}},
    ("GET", "/api/coord/http-requests"): {"query": {"limit": "100", "offset": "0", "q": "", "root_domain": ""}},
    ("GET", "/api/coord/discovered-targets"): {"query": {"limit": "200", "offset": "0", "q": "", "sort_key": "saved_at_utc", "sort_dir": "desc"}},
    ("GET", "/api/coord/discovered-target-sitemap"): {"query": {"root_domain": "", "limit": "200", "offset": "0", "q": ""}},
    ("GET", "/api/coord/discovered-target-response"): {"query": {"root_domain": "", "url": ""}},
    ("GET", "/api/coord/discovered-files"): {"query": {"limit": "5000", "q": ""}},
    ("GET", "/api/coord/high-value-files"): {"query": {"limit": "5000", "q": ""}},
    ("GET", "/api/coord/extractor-matches"): {"query": {"limit": "500", "offset": "0", "q": "", "root_domain": ""}},
    ("GET", "/api/coord/fuzzing"): {"query": {"limit": "500", "offset": "0", "q": "", "root_domain": ""}},
    ("GET", "/api/coord/workflow-config"): {"query": {"workflow_id": "run-recon"}},
    ("GET", "/api/coord/workflow-snapshot"): {"query": {"limit": "2000"}},
    ("GET", "/api/coord/workflow-domains"): {"query": {"limit": "2000"}},
    ("GET", "/api/coord/workflow-domain"): {"query": {"root_domain": ""}},
    ("GET", "/api/coord/artifact"): {"query": {"root_domain": "", "artifact_type": "", "include_content": "true"}},
    ("GET", "/api/coord/artifact/manifest-entries"): {"query": {"root_domain": "", "artifact_type": "", "limit": "1000"}},
    ("GET", "/api/coord/artifacts"): {"query": {"root_domain": ""}},
    ("POST", "/api/coord/register-targets"): {"body": {"targets": ["example.com"], "replace_existing": False}},
    ("POST", "/api/coord/claim"): {"body": {"worker_id": "debugger", "lease_seconds": 30}},
    ("POST", "/api/coord/heartbeat"): {"body": {"entry_id": "", "worker_id": "debugger", "lease_seconds": 30}},
    ("POST", "/api/coord/complete"): {"body": {"entry_id": "", "worker_id": "debugger", "exit_code": 0, "error": ""}},
    ("POST", "/api/coord/session"): {"body": {"session": {"root_domain": "example.com", "start_url": "https://example.com", "max_pages": 1}}},
    ("POST", "/api/coord/stage/enqueue"): {"body": {"root_domain": "example.com", "stage": "", "workflow_id": "run-recon", "worker_id": "debugger", "reason": "api_debugger"}},
    ("POST", "/api/coord/stage/claim"): {"body": {"stage": "", "worker_id": "debugger", "lease_seconds": 30}},
    ("POST", "/api/coord/stage/claim-next"): {"body": {"worker_id": "debugger", "lease_seconds": 30, "plugin_allowlist": []}},
    ("POST", "/api/coord/stage/heartbeat"): {"body": {"root_domain": "example.com", "stage": "", "worker_id": "debugger", "lease_seconds": 30, "workflow_id": "run-recon"}},
    ("POST", "/api/coord/stage/progress"): {"body": {"root_domain": "example.com", "stage": "", "worker_id": "debugger", "workflow_id": "run-recon", "progress": {"status": "debug"}}},
    ("POST", "/api/coord/stage/complete"): {"body": {"root_domain": "example.com", "stage": "", "worker_id": "debugger", "workflow_id": "run-recon", "exit_code": 0, "error": ""}},
    ("POST", "/api/coord/stage/reset"): {"body": {"workflow_id": "run-recon", "root_domains": [], "plugins": [], "statuses": ["failed"], "hard_delete": False}},
    ("POST", "/api/coord/stage/control"): {"body": {"workflow_id": "run-recon", "root_domain": "example.com", "stage": "", "action": "pause"}},
    ("POST", "/api/coord/targets/reset"): {"body": {"root_domains": [], "statuses": ["failed"], "hard_delete": False}},
    ("POST", "/api/coord/tasks/reset"): {"body": {"scope": "stage_tasks", "workflow_id": "run-recon", "statuses": ["failed"], "hard_delete": False}},
    ("POST", "/api/coord/artifact"): {"body": {"root_domain": "example.com", "artifact_type": "debug", "content_base64": "", "source_worker": "api_debugger", "media_type": "text/plain"}},
    ("POST", "/api/coord/artifact/stream"): {"query": {"root_domain": "example.com", "artifact_type": "debug", "source_worker": "api_debugger", "media_type": "text/plain"}, "body": "debug content"},
    ("POST", "/api/coord/workflow/run"): {"body": {"workflow_id": "run-recon", "root_domains": ["example.com"], "plugins": [], "reason": "api_debugger", "notify_workers": False}},
    ("POST", "/api/coord/workflow/run/cancel"): {"body": {"workflow_run_id": "", "actor": "api_debugger", "reason": "Canceled from API Debugger"}},
    ("POST", "/api/coord/workflow/tasks/retry-failed"): {"body": {"workflow_id": "run-recon", "workflow_run_id": "", "actor": "api_debugger", "reason": "Retry from API Debugger", "limit": 5000}},
    ("POST", "/api/coord/workers/command"): {"body": {"worker_id": "", "command": "status", "payload": {}}},
    ("POST", "/api/coord/worker-command/claim"): {"body": {"worker_id": "debugger", "worker_state": "idle"}},
    ("POST", "/api/coord/worker-command/complete"): {"body": {"worker_id": "debugger", "command_id": 0, "success": True, "error": ""}},
    ("POST", "/api/regenerate-master-report"): {"body": {}},
}


def _source_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _line_number_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _decorator_endpoints(source: str, file_label: str) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return endpoints

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "app"
                and func.attr.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}
            ):
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant) or not isinstance(decorator.args[0].value, str):
                continue
            path = decorator.args[0].value
            endpoints.append(
                {
                    "method": func.attr.upper(),
                    "path": path,
                    "handler": node.name,
                    "source": file_label,
                    "line": getattr(node, "lineno", 0),
                }
            )
    return endpoints


def _literal_route_endpoints(source: str, file_label: str, method: str, segment: str) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    for match in re.finditer(r"path\s*==\s*[\"'](/api/[^\"']+)[\"']", segment):
        path = match.group(1)
        endpoints.append(
            {
                "method": method,
                "path": path,
                "handler": "",
                "source": file_label,
                "line": _line_number_for_offset(source, source.find(segment) + match.start()),
            }
        )
    for match in re.finditer(r"path\.startswith\(\s*[\"'](/api/[^\"']+)[\"']\s*\)", segment):
        path = match.group(1)
        endpoints.append(
            {
                "method": method,
                "path": path,
                "handler": "",
                "source": file_label,
                "line": _line_number_for_offset(source, source.find(segment) + match.start()),
                "path_is_prefix": True,
            }
        )
    return endpoints


def _server_endpoints(source: str, file_label: str) -> list[dict[str, Any]]:
    get_start = source.find("def do_GET")
    post_start = source.find("def do_POST")
    options_start = source.find("def do_OPTIONS")
    endpoints: list[dict[str, Any]] = []
    if get_start >= 0:
        get_end = post_start if post_start > get_start else len(source)
        endpoints.extend(_literal_route_endpoints(source, file_label, "GET", source[get_start:get_end]))
    if post_start >= 0:
        post_end = options_start if options_start > post_start else len(source)
        endpoints.extend(_literal_route_endpoints(source, file_label, "POST", source[post_start:post_end]))
    return endpoints


def _dedupe(endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for endpoint in endpoints:
        method = str(endpoint.get("method") or "GET").upper()
        path = str(endpoint.get("path") or "").strip()
        if not path.startswith("/api/"):
            continue
        key = (method, path)
        existing = by_key.get(key)
        if existing:
            sources = set(str(item).strip() for item in existing.get("sources", []) if str(item).strip())
            source = str(endpoint.get("source") or "").strip()
            if source:
                sources.add(source)
            existing["sources"] = sorted(sources)
            if not existing.get("handler") and endpoint.get("handler"):
                existing["handler"] = endpoint.get("handler")
            continue
        base = {
            "method": method,
            "path": path,
            "label": f"{method} {path}",
            "description": "",
            "query": {},
            "headers": {"Accept": "application/json"},
            "body": None,
            "auth": path.startswith("/api/coord/") and path not in {"/api/coord/ping"},
            "source": str(endpoint.get("source") or ""),
            "sources": [str(endpoint.get("source") or "")],
            "line": int(endpoint.get("line") or 0),
            "handler": str(endpoint.get("handler") or ""),
            "path_is_prefix": bool(endpoint.get("path_is_prefix")),
        }
        override = _ENDPOINT_SAMPLE_OVERRIDES.get(key)
        if override:
            base.update(override)
            if "headers" not in override:
                base["headers"] = {"Accept": "application/json"}
        if method in {"POST", "PUT", "PATCH"} and base.get("body") is None:
            base["body"] = {}
            base["headers"] = {**dict(base.get("headers") or {}), "Content-Type": "application/json", "Accept": "application/json"}
        by_key[key] = base
    return sorted(by_key.values(), key=lambda item: (str(item.get("path") or ""), str(item.get("method") or "")))


def build_api_debugger_catalog(base_dir: Path | None = None) -> dict[str, Any]:
    """Build a browser-friendly catalog of API endpoints discovered from app sources."""
    root = Path(base_dir or Path(__file__).resolve().parents[2]).resolve()
    server_source = _source_text(root / "server.py")
    fastapi_source = _source_text(root / "app_platform" / "server" / "fastapi_app.py")
    endpoints = _server_endpoints(server_source, "server.py")
    endpoints.extend(_decorator_endpoints(fastapi_source, "app_platform/server/fastapi_app.py"))
    items = _dedupe(endpoints)
    return {
        "ok": True,
        "generated_from": ["server.py", "app_platform/server/fastapi_app.py"],
        "endpoint_count": len(items),
        "endpoints": items,
        "methods": list(HTTP_METHODS),
        "default_headers": {"Accept": "application/json"},
    }


def build_api_debugger_catalog_json(base_dir: Path | None = None) -> str:
    return json.dumps(build_api_debugger_catalog(base_dir), ensure_ascii=False, sort_keys=True)
