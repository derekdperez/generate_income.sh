
from __future__ import annotations

import copy
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from auth0r.canonicalize import likely_state_changing


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


def _marker_hits(text: str, markers: list[Any]) -> list[str]:
    haystack = text or ""
    hits = []
    for marker in markers or []:
        value = getattr(marker, "value", "")
        if value and value in haystack:
            hits.append(value)
    return hits


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
) -> list[tuple[str, httpx.Response | None, list[str], list[str]]]:
    method = str(action.get("method", "GET")).upper()
    url = str(action.get("url", ""))
    headers = dict(action.get("headers", {}) or {})
    body = action.get("body")
    variants = []

    def send(client: httpx.Client, variant: str):
        throttle.wait()
        rsp = client.request(method, url, headers=headers, content=body)
        variants.append((variant, rsp, _marker_hits(rsp.text, success_markers), _marker_hits(rsp.text, denial_markers)))

    send(baseline_client, "original")

    no_cookie_client = httpx.Client(follow_redirects=True, verify=verify_tls, timeout=timeout_seconds, headers=baseline_client.headers)
    send(no_cookie_client, "no_cookies")
    no_cookie_client.close()

    stripped_auth_headers = {k: v for k, v in headers.items() if k.lower() not in {"authorization", "x-api-key", "cookie"}}
    stripped_client = httpx.Client(follow_redirects=True, verify=verify_tls, timeout=timeout_seconds, headers=baseline_client.headers)
    throttle.wait()
    rsp = stripped_client.request(method, url, headers=stripped_auth_headers, content=body)
    variants.append(("stripped_auth_headers", rsp, _marker_hits(rsp.text, success_markers), _marker_hits(rsp.text, denial_markers)))
    stripped_client.close()

    cookies = _clone_cookies(baseline_client)
    if cookies:
        session_only = httpx.Client(follow_redirects=True, verify=verify_tls, timeout=timeout_seconds, headers=baseline_client.headers)
        c = cookies[0]
        session_only.cookies.set(c["name"], c["value"], domain=c["domain"] or None, path=c["path"] or "/")
        send(session_only, "one_cookie_removed")
        session_only.close()

    if logout_url:
        post_logout_client = httpx.Client(follow_redirects=True, verify=verify_tls, timeout=timeout_seconds, headers=baseline_client.headers)
        for c in cookies:
            post_logout_client.cookies.set(c["name"], c["value"], domain=c["domain"] or None, path=c["path"] or "/")
        throttle.wait()
        try:
            post_logout_client.get(logout_url)
        except Exception:
            pass
        send(post_logout_client, "post_logout_old_session")
        post_logout_client.close()

    return variants
