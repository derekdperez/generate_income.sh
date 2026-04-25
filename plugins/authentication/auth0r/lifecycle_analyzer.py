
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


SESSION_COOKIE_HINTS = {"session", "sess", "sid", "jsessionid", "phpsessid", "connect.sid", "auth", "token"}


def _cookie_fingerprint(client: httpx.Client) -> str:
    items = []
    for cookie in client.cookies.jar:
        items.append(f"{cookie.name}={cookie.value}")
    return "|".join(sorted(items))


def _session_cookie_names(client: httpx.Client) -> list[str]:
    names: list[str] = []
    for cookie in client.cookies.jar:
        lowered = cookie.name.lower()
        if any(hint in lowered for hint in SESSION_COOKIE_HINTS):
            names.append(cookie.name)
    return sorted(set(names))


@dataclass
class LifecycleAssessment:
    subtype: str
    suspicious: bool
    confidence: float
    severity: str
    title: str
    summary: dict[str, Any]


def assess_login_rotation(pre_login_cookies: list[dict[str, Any]], authenticated_client: httpx.Client) -> LifecycleAssessment:
    before = {str(item.get("name", "")): str(item.get("value", "")) for item in pre_login_cookies or [] if isinstance(item, dict)}
    after = {cookie.name: cookie.value for cookie in authenticated_client.cookies.jar}
    shared = sorted(name for name in set(before) & set(after) if before.get(name) == after.get(name))
    session_names = _session_cookie_names(authenticated_client)
    rotated = True
    if session_names:
        rotated = not all(name in shared for name in session_names)
    return LifecycleAssessment(
        subtype="login_rotation",
        suspicious=not rotated,
        confidence=0.92 if not rotated else 0.35,
        severity="medium" if not rotated else "info",
        title=("Session identifiers did not rotate on login" if not rotated else "Session identifiers rotated on login"),
        summary={
            "pre_login_cookie_names": sorted(before.keys()),
            "post_login_cookie_names": sorted(after.keys()),
            "session_cookie_names": session_names,
            "unchanged_cookie_names": shared,
            "rotated": rotated,
        },
    )


def assess_parallel_sessions(primary_client: httpx.Client, secondary_client: httpx.Client) -> LifecycleAssessment:
    primary_fp = _cookie_fingerprint(primary_client)
    secondary_fp = _cookie_fingerprint(secondary_client)
    different = bool(primary_fp) and bool(secondary_fp) and primary_fp != secondary_fp
    return LifecycleAssessment(
        subtype="parallel_session_overlap",
        suspicious=not different,
        confidence=0.85 if not different else 0.40,
        severity="medium" if not different else "info",
        title=("Fresh sessions reused identical session state" if not different else "Fresh sessions produced distinct session state"),
        summary={
            "primary_cookie_names": sorted(cookie.name for cookie in primary_client.cookies.jar),
            "secondary_cookie_names": sorted(cookie.name for cookie in secondary_client.cookies.jar),
            "primary_fingerprint": primary_fp,
            "secondary_fingerprint": secondary_fp,
            "distinct": different,
        },
    )


def assess_logout_invalidation(
    old_session_response: httpx.Response | None,
    *,
    denial_hits: list[str],
    success_hits: list[str],
) -> LifecycleAssessment:
    status = getattr(old_session_response, "status_code", None)
    suspicious = bool(old_session_response is not None and success_hits and not denial_hits and status and status < 400)
    return LifecycleAssessment(
        subtype="logout_invalidation",
        suspicious=suspicious,
        confidence=0.90 if suspicious else 0.45,
        severity="high" if suspicious else "info",
        title=("Old session remained usable after logout" if suspicious else "Old session was not usable after logout"),
        summary={
            "status_code": status,
            "authenticated_markers_matched": list(success_hits or []),
            "denial_markers_matched": list(denial_hits or []),
            "suspicious": suspicious,
        },
    )
