
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

VOLATILE_KEYS = {"csrf", "_csrf", "token", "timestamp", "_", "nonce", "session", "phpsessid"}


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    qs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in VOLATILE_KEYS]
    qs.sort()
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.params, urlencode(qs), ""))


def likely_state_changing(method: str) -> bool:
    return str(method or "GET").upper() not in {"GET", "HEAD", "OPTIONS"}
