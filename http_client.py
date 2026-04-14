#!/usr/bin/env python3
from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_USER_AGENT = "nightmare-httpx/1.0"

_CLIENT_LOCK = threading.Lock()
_CLIENTS: dict[tuple[bool, float, bool, str], httpx.Client] = {}


def _client_key(verify: bool, timeout_seconds: float, follow_redirects: bool, user_agent: str) -> tuple[bool, float, bool, str]:
    return (bool(verify), float(timeout_seconds), bool(follow_redirects), str(user_agent or DEFAULT_USER_AGENT))


def get_shared_client(
    *,
    verify: bool = True,
    timeout_seconds: float = 30.0,
    follow_redirects: bool = True,
    user_agent: str = DEFAULT_USER_AGENT,
) -> httpx.Client:
    key = _client_key(verify, timeout_seconds, follow_redirects, user_agent)
    with _CLIENT_LOCK:
        client = _CLIENTS.get(key)
        if client is not None:
            return client
        timeout = httpx.Timeout(timeout_seconds)
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        client = httpx.Client(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=verify,
            headers={"User-Agent": user_agent},
            limits=limits,
        )
        _CLIENTS[key] = client
        return client


def close_shared_clients() -> None:
    with _CLIENT_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        try:
            client.close()
        except Exception:
            pass


@dataclass
class CappedResponse:
    status_code: int
    url: str
    headers: dict[str, str]
    body: bytes
    elapsed_ms: int


def read_response_body_capped(response: httpx.Response, limit: int) -> bytes:
    if limit <= 0:
        return b""
    chunks: list[bytes] = []
    remaining = int(limit)
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        if len(chunk) <= remaining:
            chunks.append(chunk)
            remaining -= len(chunk)
        else:
            chunks.append(chunk[:remaining])
            remaining = 0
        if remaining <= 0:
            break
    return b"".join(chunks)


def request_capped(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    timeout_seconds: float = 30.0,
    read_limit: int = 4096,
    follow_redirects: bool = True,
    verify: bool = True,
    client: httpx.Client | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    cookies: Any = None,
) -> CappedResponse:
    owned_client = client is None
    http = client or get_shared_client(
        verify=verify,
        timeout_seconds=timeout_seconds,
        follow_redirects=follow_redirects,
        user_agent=user_agent,
    )
    request_headers = dict(headers or {})
    if "User-Agent" not in {str(k): str(v) for k, v in request_headers.items()}:
        request_headers.setdefault("User-Agent", user_agent)
    t0 = time.perf_counter()
    with http.stream(
        method.upper(),
        url,
        headers=request_headers,
        content=content,
        timeout=timeout_seconds,
        cookies=cookies,
    ) as response:
        body = read_response_body_capped(response, read_limit)
        elapsed_ms = int(round((time.perf_counter() - t0) * 1000.0))
        return CappedResponse(
            status_code=int(response.status_code),
            url=str(response.url),
            headers=dict(response.headers.items()),
            body=body,
            elapsed_ms=elapsed_ms,
        )


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_payload: dict[str, Any] | None = None,
    timeout_seconds: float = 30.0,
    verify: bool = True,
    follow_redirects: bool = True,
    user_agent: str = DEFAULT_USER_AGENT,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    owned_client = client is None
    http = client or get_shared_client(
        verify=verify,
        timeout_seconds=timeout_seconds,
        follow_redirects=follow_redirects,
        user_agent=user_agent,
    )
    request_headers = dict(headers or {})
    try:
        response = http.request(
            method.upper(),
            url,
            headers=request_headers,
            json=json_payload,
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Network error {method.upper()} {url}: {exc}") from exc
    text = response.text
    if response.is_error:
        raise RuntimeError(f"HTTP {response.status_code} {method.upper()} {url}: {text[:400]}")
    try:
        parsed = json.loads(text or "{}")
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}
