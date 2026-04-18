from __future__ import annotations

import hashlib
import html
import re
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .pattern_library import (
    BASE64ISH_RE,
    FUZZ_MARKER_RE,
    HEX_RE,
    HTML_COMMENT_RE,
    ISO_DATETIME_RE,
    JWT_RE,
    LONG_INT_RE,
    MEMORY_ADDRESS_RE,
    RFC_DATETIME_RE,
    REQUEST_ID_PAIR_RE,
    SOFT_LOGIN_RE,
    STACKTRACE_LINE_NUM_RE,
    UUID_RE,
    VOLATILE_HEADERS,
)
from .schemas import NormalizedResponse

_TOKEN_KEYS = {"token", "csrf", "session", "nonce", "request_id", "trace_id"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _lower_header_dict(raw_headers: dict[str, Any] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for k, v in dict(raw_headers or {}).items():
        key = str(k or "").strip().lower()
        if not key:
            continue
        if isinstance(v, (list, tuple)):
            vals = [str(item or "").strip() for item in v if str(item or "").strip()]
        else:
            vals = [str(v or "").strip()]
        for item in vals:
            if item:
                out[key].append(re.sub(r"\s+", " ", item))
    # Deterministic ordering and de-dup.
    normalized: dict[str, list[str]] = {}
    for key in sorted(out.keys()):
        dedup = sorted(set(out[key]))
        normalized[key] = dedup
    return normalized


def _parse_content_type(value: str) -> tuple[str, str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return "", ""
    parts = [item.strip() for item in raw.split(";") if item.strip()]
    mime = parts[0] if parts else ""
    charset = ""
    for item in parts[1:]:
        if item.startswith("charset="):
            charset = item.split("=", 1)[1].strip()
            break
    return mime, charset


def _parse_cache_control(value: str) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    return {item.strip() for item in text.split(",") if item.strip()}


def _parse_set_cookie_semantics(values: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        pieces = [item.strip() for item in text.split(";") if item.strip()]
        if not pieces:
            continue
        name = pieces[0].split("=", 1)[0].strip().lower()
        attrs = sorted(
            item.split("=", 1)[0].strip().lower()
            for item in pieces[1:]
            if item.split("=", 1)[0].strip()
        )
        out.append({"name": name, "attrs": attrs})
    out.sort(key=lambda item: (item.get("name", ""), ",".join(item.get("attrs", []))))
    return out


def _looks_dynamic_query_value(value: str) -> bool:
    text = str(value or "")
    if len(text) >= 24 and (HEX_RE.search(text) or BASE64ISH_RE.search(text) or JWT_RE.search(text)):
        return True
    if UUID_RE.search(text):
        return True
    if len(text) >= 8 and text.isdigit():
        return True
    return False


def normalize_location(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        cleaned: list[tuple[str, str]] = []
        for key, raw_val in pairs:
            key_norm = str(key or "").strip().lower()
            val_norm = str(raw_val or "").strip()
            if key_norm in _TOKEN_KEYS or _looks_dynamic_query_value(val_norm):
                cleaned.append((key, "<TOKEN>"))
            else:
                cleaned.append((key, val_norm))
        cleaned.sort(key=lambda item: (item[0], item[1]))
        query = urlencode(cleaned, doseq=True)
        return urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path,
                parsed.params,
                query,
                "",
            )
        )
    except Exception:
        return text


def normalize_body_text(text: str, *, request_url: str = "") -> str:
    out = str(text or "")
    # Strip HTML comments first to avoid noisy counters and IDs.
    out = HTML_COMMENT_RE.sub("", out)
    # Deterministic placeholder replacement order.
    out = JWT_RE.sub("<JWT>", out)
    out = UUID_RE.sub("<UUID>", out)
    out = ISO_DATETIME_RE.sub("<DATETIME>", out)
    out = RFC_DATETIME_RE.sub("<DATETIME>", out)
    out = REQUEST_ID_PAIR_RE.sub("<TOKEN_PAIR>", out)
    out = MEMORY_ADDRESS_RE.sub("<ADDR>", out)
    out = STACKTRACE_LINE_NUM_RE.sub(r"\1<NUM>", out)
    out = HEX_RE.sub("<HEX>", out)
    out = BASE64ISH_RE.sub("<TOKEN>", out)
    out = LONG_INT_RE.sub("<NUM>", out)
    if request_url:
        parsed = urlparse(request_url)
        req_path = str(parsed.path or "").strip()
        if req_path and req_path != "/":
            out = out.replace(req_path, "<REQ_PATH>")
    out = html.unescape(out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _tokenize(text: str) -> set[str]:
    items = re.findall(r"[A-Za-z0-9_:/.-]{3,}", str(text or "").lower())
    return {item for item in items if item not in {"the", "and", "for", "with", "from"}}


def normalize_response(
    response: dict[str, Any] | None,
    *,
    request_url: str = "",
) -> NormalizedResponse:
    payload = dict(response or {})
    raw_headers = _lower_header_dict(payload.get("response_headers") if isinstance(payload.get("response_headers"), dict) else {})
    comparable_headers: dict[str, str] = {}
    for key, values in raw_headers.items():
        if key in VOLATILE_HEADERS:
            continue
        comparable_headers[key] = ", ".join(sorted(values))
    status_code = _safe_int(payload.get("status", 0), 0)
    elapsed_ms = _safe_int(payload.get("elapsed_ms", 0), 0)
    url = str(payload.get("url", "") or request_url or "")
    content_type_raw = ", ".join(raw_headers.get("content-type", []))
    content_type_mime, content_type_charset = _parse_content_type(content_type_raw)
    body_raw_text = str(payload.get("body_preview", "") or "")
    body_normalized_text = normalize_body_text(body_raw_text, request_url=url or request_url)
    location_normalized = normalize_location(", ".join(raw_headers.get("location", [])))
    cache_control_directives = _parse_cache_control(", ".join(raw_headers.get("cache-control", [])))
    set_cookie_semantics = _parse_set_cookie_semantics(raw_headers.get("set-cookie", []))
    token_set = _tokenize(body_normalized_text)
    return NormalizedResponse(
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        url=url,
        content_type_raw=content_type_raw,
        content_type_mime=content_type_mime,
        content_type_charset=content_type_charset,
        headers=raw_headers,
        comparable_headers=comparable_headers,
        cache_control_directives=cache_control_directives,
        set_cookie_semantics=set_cookie_semantics,
        location_normalized=location_normalized,
        body_raw_text=body_raw_text,
        body_normalized_text=body_normalized_text,
        body_raw_hash=hashlib.sha256(body_raw_text.encode("utf-8", errors="replace")).hexdigest(),
        body_normalized_hash=hashlib.sha256(body_normalized_text.encode("utf-8", errors="replace")).hexdigest(),
        body_length=len(body_raw_text),
        body_line_count=max(1, body_raw_text.count("\n") + 1) if body_raw_text else 0,
        token_set=token_set,
    )


def extract_marker_candidates(mutated_value: str) -> list[str]:
    text = str(mutated_value or "")
    markers = sorted(set(FUZZ_MARKER_RE.findall(text)))
    if markers:
        return markers
    if text and len(text) >= 4:
        return [text]
    return []


def seems_login_like(text: str) -> bool:
    return bool(SOFT_LOGIN_RE.search(str(text or "")))

