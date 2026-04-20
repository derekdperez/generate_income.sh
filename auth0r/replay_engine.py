
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

SESSION_COOKIE_HINTS = {"session", "sess", "sid", "jsessionid", "phpsessid", "connect.sid", "auth", "token"}
CSRF_HEADER_HINTS = {"x-csrf-token", "x-xsrf-token", "csrf-token", "x-csrftoken", "x-request-verification-token"}
CSRF_FIELD_PATTERNS = [re.compile(r"csrf", re.I), re.compile(r"xsrf", re.I), re.compile(r"authenticity_token", re.I)]


def _clone_cookies(client: httpx.Client) -> list[dict[str, Any]]:
    out = []
    for cookie in client.cookies.jar:
        out.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
            }
        )
    return out


def _build_client_from_cookies(
    cookies: list[dict[str, Any]],
    *,
    verify_tls: bool,
    timeout_seconds: float,
    headers: dict[str, str],
) -> httpx.Client:
    client = httpx.Client(follow_redirects=True, verify=verify_tls, timeout=timeout_seconds, headers=headers)
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name", "") or "")
        if not name:
            continue
        client.cookies.set(
            name,
            str(cookie.get("value", "") or ""),
            domain=(str(cookie.get("domain", "") or "") or None),
            path=(str(cookie.get("path", "/") or "/") or "/"),
        )
    return client


def _marker_hits(text: str, markers: list[Any]) -> list[str]:
    haystack = text or ""
    hits = []
    for marker in markers or []:
        value = getattr(marker, "value", "")
        if value and value in haystack:
            hits.append(value)
    return hits


def _request_payload(action: dict[str, Any]) -> tuple[dict[str, str], Any]:
    headers = dict(action.get("headers", {}) or {})
    body = action.get("body")
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
        headers.setdefault("content-type", "application/json")
    return headers, body


def _strip_auth_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in {"authorization", "x-api-key", "cookie"}}


def _session_cookie_names(cookies: list[dict[str, Any]]) -> set[str]:
    names = set()
    for cookie in cookies:
        lowered = str(cookie.get("name", "")).lower()
        if any(hint in lowered for hint in SESSION_COOKIE_HINTS):
            names.add(str(cookie.get("name", "")))
    return names


def _strip_csrf(headers: dict[str, str], body: Any) -> tuple[dict[str, str], Any]:
    stripped_headers = {k: v for k, v in headers.items() if k.lower() not in CSRF_HEADER_HINTS}
    if isinstance(body, str):
        if stripped_headers.get("content-type", "").lower().startswith("application/json"):
            try:
                payload = json.loads(body)
            except Exception:
                return stripped_headers, body
            if isinstance(payload, dict):
                payload = {k: v for k, v in payload.items() if not any(p.search(k) for p in CSRF_FIELD_PATTERNS)}
                return stripped_headers, json.dumps(payload)
        return stripped_headers, body
    if isinstance(body, dict):
        body = {k: v for k, v in body.items() if not any(p.search(str(k)) for p in CSRF_FIELD_PATTERNS)}
    return stripped_headers, body


class DomainThrottle:
    def __init__(self, min_delay_seconds: float = 0.25):
        self.min_delay_seconds = max(0.25, float(min_delay_seconds or 0.25))
        self._last = 0.0

    def wait(self):
        now = time.monotonic()
        delta = now - self._last
        if delta < self.min_delay_seconds:
            time.sleep(self.min_delay_seconds - delta)
        self._last = time.monotonic()


def replay_variants(
    baseline_client: httpx.Client,
    action: dict[str, Any],
    *,
    throttle: DomainThrottle,
    timeout_seconds: float,
    verify_tls: bool,
    success_markers: list[Any],
    denial_markers: list[Any],
    logout_url: str = "",
    secondary_client: httpx.Client | None = None,
    cross_identity_client: httpx.Client | None = None,
) -> list[tuple[str, httpx.Response | None, list[str], list[str]]]:
    method = str(action.get("method", "GET")).upper()
    url = str(action.get("url", ""))
    headers, body = _request_payload(action)
    variants: list[tuple[str, httpx.Response | None, list[str], list[str]]] = []

    def send(client: httpx.Client, variant: str, *, headers_override: dict[str, str] | None = None, body_override: Any = None):
        throttle.wait()
        rsp = client.request(method, url, headers=(headers_override if headers_override is not None else headers), content=(body if body_override is None else body_override))
        variants.append((variant, rsp, _marker_hits(rsp.text, success_markers), _marker_hits(rsp.text, denial_markers)))

    send(baseline_client, "original")

    no_cookie_client = httpx.Client(follow_redirects=True, verify=verify_tls, timeout=timeout_seconds, headers=baseline_client.headers)
    send(no_cookie_client, "no_cookies")
    no_cookie_client.close()

    stripped_client = httpx.Client(follow_redirects=True, verify=verify_tls, timeout=timeout_seconds, headers=baseline_client.headers)
    send(stripped_client, "stripped_auth_headers", headers_override=_strip_auth_headers(headers))
    stripped_client.close()

    cookies = _clone_cookies(baseline_client)
    if cookies:
        removed = cookies[1:] if len(cookies) > 1 else []
        one_cookie_removed_client = _build_client_from_cookies(removed, verify_tls=verify_tls, timeout_seconds=timeout_seconds, headers=dict(baseline_client.headers))
        send(one_cookie_removed_client, "one_cookie_removed")
        one_cookie_removed_client.close()

        session_names = _session_cookie_names(cookies)
        session_cookies = [c for c in cookies if c.get("name") in session_names] or cookies[:1]
        auxiliary_cookies = [c for c in cookies if c.get("name") not in session_names]

        session_only_client = _build_client_from_cookies(session_cookies, verify_tls=verify_tls, timeout_seconds=timeout_seconds, headers=dict(baseline_client.headers))
        send(session_only_client, "session_cookie_only")
        session_only_client.close()

        if auxiliary_cookies:
            auxiliary_only_client = _build_client_from_cookies(auxiliary_cookies, verify_tls=verify_tls, timeout_seconds=timeout_seconds, headers=dict(baseline_client.headers))
            send(auxiliary_only_client, "auxiliary_cookie_only")
            auxiliary_only_client.close()

    missing_csrf_client = _build_client_from_cookies(cookies, verify_tls=verify_tls, timeout_seconds=timeout_seconds, headers=dict(baseline_client.headers))
    stripped_csrf_headers, stripped_csrf_body = _strip_csrf(headers, body)
    send(missing_csrf_client, "missing_csrf", headers_override=stripped_csrf_headers, body_override=stripped_csrf_body)
    missing_csrf_client.close()

    if secondary_client is not None:
        send(secondary_client, "second_fresh_session")

    if cross_identity_client is not None:
        send(cross_identity_client, "cross_identity")

    if logout_url:
        post_logout_client = _build_client_from_cookies(cookies, verify_tls=verify_tls, timeout_seconds=timeout_seconds, headers=dict(baseline_client.headers))
        throttle.wait()
        try:
            post_logout_client.get(logout_url)
        except Exception:
            pass
        send(post_logout_client, "post_logout_old_session")
        post_logout_client.close()

    return variants
