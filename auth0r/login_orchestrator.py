
from __future__ import annotations

import httpx

from auth0r.types import AuthIdentity


def _marker_matches(text: str, marker_kind: str, marker_value: str) -> bool:
    haystack = text or ""
    kind = (marker_kind or "text").strip().lower()
    needle = marker_value or ""
    if not needle:
        return False
    if kind in {"text", "contains"}:
        return needle in haystack
    return False


def _cookie_allowed(cookie: dict, identity: AuthIdentity) -> bool:
    if not identity.allowed_hosts:
        return True
    cookie_domain = str(cookie.get("domain", "") or "").lstrip(".").lower()
    if not cookie_domain:
        return True
    return any(cookie_domain == host.lower().lstrip(".") or cookie_domain.endswith("." + host.lower().lstrip(".")) for host in identity.allowed_hosts)


def verify_authenticated(client: httpx.Client, identity: AuthIdentity, base_url: str) -> tuple[bool, dict]:
    probe_url = identity.authenticated_probe_url or base_url
    try:
        response = client.get(probe_url, follow_redirects=True)
        body = response.text
    except Exception as exc:
        return False, {"error": str(exc), "probe_url": probe_url}
    success_hits = [
        marker.value for marker in identity.success_markers
        if _marker_matches(body, marker.kind, marker.value)
    ]
    denial_hits = [
        marker.value for marker in identity.denial_markers
        if _marker_matches(body, marker.kind, marker.value)
    ]
    verified = bool(success_hits) and not denial_hits
    return verified, {
        "probe_url": probe_url,
        "status_code": response.status_code,
        "success_markers_matched": success_hits,
        "denial_markers_matched": denial_hits,
    }


def establish_session(identity: AuthIdentity, base_url: str, verify_tls: bool = True, timeout_seconds: float = 20.0) -> tuple[httpx.Client, str, dict]:
    headers = dict(identity.custom_headers or {})
    client = httpx.Client(follow_redirects=True, verify=verify_tls, timeout=timeout_seconds, headers=headers)
    pre_login_cookies = []
    source_type = "imported_cookie" if identity.imported_cookies and identity.login_strategy == "cookie_import" else "fresh_login"
    for cookie in identity.imported_cookies:
        if not isinstance(cookie, dict) or not _cookie_allowed(cookie, identity):
            continue
        name = str(cookie.get("name", "") or "")
        value = str(cookie.get("value", "") or "")
        domain = str(cookie.get("domain", "") or "")
        path = str(cookie.get("path", "/") or "/")
        if name:
            client.cookies.set(name, value, domain=domain or None, path=path)
            pre_login_cookies.append({"name": name, "value": value, "domain": domain, "path": path})
    login_cfg = {
        "login_url": identity.login_url,
        "login_method": identity.login_method,
        "username_field": identity.login_username_field,
        "password_field": identity.login_password_field,
        "extra_fields": identity.login_extra_fields,
        "pre_login_cookie_count": len(pre_login_cookies),
    }
    if identity.login_strategy in {"html_form", "json_api"} and identity.login_url:
        payload = dict(identity.login_extra_fields or {})
        payload[identity.login_username_field] = identity.username
        payload[identity.login_password_field] = identity.password
        if identity.login_strategy == "json_api":
            rsp = client.request(identity.login_method or "POST", identity.login_url, json=payload)
        else:
            rsp = client.request(identity.login_method or "POST", identity.login_url, data=payload)
        login_cfg["login_status_code"] = rsp.status_code
    verified, summary = verify_authenticated(client, identity, base_url)
    summary["session_cookie_names"] = [cookie.name for cookie in client.cookies.jar]
    summary["pre_login_cookies"] = pre_login_cookies
    summary["source_type"] = source_type
    if not verified:
        client.close()
        raise RuntimeError(f"authentication verification failed for {identity.identity_label}: {summary}")
    return client, source_type, summary
