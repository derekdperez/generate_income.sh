from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

from .pattern_library import (
    ERROR_KEYWORDS_BY_CATEGORY,
    FUZZ_MARKER_RE,
    GENERIC_ERROR_TITLE_RE,
    HTML_FORM_RE,
    HTML_SCRIPT_RE,
    HTML_TAG_RE,
    HTML_TITLE_RE,
    JAVA_CAUSED_BY_RE,
    JAVA_EXCEPTION_RE,
    JAVA_STACK_LINE_RE,
    SECURITY_HEADERS,
    SQL_ERROR_RE,
    SPRING_WHITELABEL_RE,
    TOMCAT_JETTY_RE,
    XML_ATTR_RE,
    XML_TAG_RE,
)
from .schemas import NormalizedResponse, ResponseFeatures


def _json_key_paths(value: Any, prefix: str = "$") -> set[str]:
    out: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            node = f"{prefix}.{key_text}"
            out.add(node)
            out |= _json_key_paths(item, node)
    elif isinstance(value, list):
        out.add(f"{prefix}[]")
        for item in value[:20]:
            out |= _json_key_paths(item, f"{prefix}[]")
    return out


def _extract_json_features(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"parse_ok": False, "top_level_type": "", "key_paths": [], "error_like_keys": []}
    try:
        parsed = json.loads(text)
    except Exception:
        return payload
    payload["parse_ok"] = True
    payload["top_level_type"] = type(parsed).__name__
    payload["key_paths"] = sorted(_json_key_paths(parsed))
    interesting = {"error", "message", "details", "stacktrace", "exception", "trace", "errors"}
    key_paths = payload["key_paths"]
    payload["error_like_keys"] = sorted([key for key in key_paths if any(part in key.lower() for part in interesting)])
    return payload


def _extract_html_features(text: str) -> dict[str, Any]:
    title_match = HTML_TITLE_RE.search(text)
    title = ""
    if title_match:
        title = re.sub(r"\s+", " ", title_match.group(1).strip())
    tag_counts = Counter(tag.lower() for tag in HTML_TAG_RE.findall(text))
    scripts = HTML_SCRIPT_RE.findall(text)
    pre_like = len(re.findall(r"<(?:pre|code|textarea)\b", text, flags=re.IGNORECASE))
    return {
        "title": title,
        "tag_counts": dict(tag_counts),
        "forms": len(HTML_FORM_RE.findall(text)),
        "script_blocks": len(scripts),
        "pre_code_blocks": pre_like,
        "has_debug_like_block": pre_like > 0 or bool(re.search(r"\bdebug\b", text, flags=re.IGNORECASE)),
        "has_error_like_title": bool(GENERIC_ERROR_TITLE_RE.search(text)),
    }


def _extract_xml_features(text: str) -> dict[str, Any]:
    features = {"parse_ok": False, "tag_names": [], "attribute_names": [], "path_signature": []}
    tag_names = sorted(set(XML_TAG_RE.findall(text)))
    attr_names = sorted(set(XML_ATTR_RE.findall(text)))
    features["tag_names"] = tag_names[:200]
    features["attribute_names"] = attr_names[:200]
    try:
        root = ElementTree.fromstring(text)
        features["parse_ok"] = True
        paths: set[str] = set()

        def _walk(node: ElementTree.Element, prefix: str) -> None:
            path = f"{prefix}/{node.tag}"
            paths.add(path)
            for child in list(node):
                _walk(child, path)

        _walk(root, "")
        features["path_signature"] = sorted(paths)[:400]
    except Exception:
        features["path_signature"] = tag_names[:50]
    return features


def _extract_text_features(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    counts = Counter(lines)
    return {
        "line_count": len(lines),
        "top_distinctive_lines": [item[0] for item in counts.most_common(5)],
        "error_marker_count": len([line for line in lines if "error" in line.lower() or "exception" in line.lower()]),
    }


def _keyword_category_counts(text: str) -> tuple[dict[str, int], list[str]]:
    low = text.lower()
    counts: dict[str, int] = {}
    present_categories: list[str] = []
    for category, words in ERROR_KEYWORDS_BY_CATEGORY.items():
        hit = 0
        for word in words:
            hit += low.count(word)
        counts[category] = hit
        if hit > 0:
            present_categories.append(category)
    if SQL_ERROR_RE.search(low):
        counts["sql_database_error"] = counts.get("sql_database_error", 0) + 2
        if "sql_database_error" not in present_categories:
            present_categories.append("sql_database_error")
    if SPRING_WHITELABEL_RE.search(low):
        counts["server_container_error"] = counts.get("server_container_error", 0) + 2
        if "server_container_error" not in present_categories:
            present_categories.append("server_container_error")
    if TOMCAT_JETTY_RE.search(low):
        counts["server_container_error"] = counts.get("server_container_error", 0) + 1
        if "server_container_error" not in present_categories:
            present_categories.append("server_container_error")
    return counts, sorted(set(present_categories))


def _extract_exception_names(text: str) -> list[str]:
    out = set(JAVA_EXCEPTION_RE.findall(text))
    out |= set(JAVA_CAUSED_BY_RE.findall(text))
    if JAVA_STACK_LINE_RE.search(text):
        out.add("java.stacktrace")
    return sorted(str(item) for item in out if str(item))


def _extract_marker_hits(
    text: str,
    *,
    marker_candidates: list[str],
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    low = text.lower()
    for marker in marker_candidates:
        token = str(marker or "")
        if not token:
            continue
        if token.lower() in low:
            hits.append({"marker": token, "context": "body_plain"})
    for match in FUZZ_MARKER_RE.finditer(text):
        hits.append({"marker": match.group(0), "context": "body_marker"})
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for item in hits:
        key = (str(item.get("marker", "")), str(item.get("context", "")))
        dedup[key] = item
    return sorted(dedup.values(), key=lambda item: (item.get("marker", ""), item.get("context", "")))


def extract_features(
    normalized: NormalizedResponse,
    *,
    marker_candidates: list[str] | None = None,
) -> ResponseFeatures:
    marker_candidates = marker_candidates or []
    body_text = normalized.body_raw_text
    low_body = body_text.lower()
    parsed_url = urlparse(normalized.url)
    redirect_target = normalized.location_normalized or ""
    security_headers = {name: name in normalized.headers for name in SECURITY_HEADERS}
    keyword_counts, error_categories = _keyword_category_counts(low_body)
    exceptions = _extract_exception_names(body_text)
    json_features: dict[str, Any] = {}
    html_features: dict[str, Any] = {}
    xml_features: dict[str, Any] = {}
    text_features = _extract_text_features(body_text)
    mime = normalized.content_type_mime
    if "json" in mime:
        json_features = _extract_json_features(body_text)
    elif "html" in mime:
        html_features = _extract_html_features(body_text)
    elif "xml" in mime:
        xml_features = _extract_xml_features(body_text)
    else:
        # Try lightweight best-effort parsing for drift detection.
        if body_text.lstrip().startswith("{") or body_text.lstrip().startswith("["):
            json_features = _extract_json_features(body_text)
        if "<html" in low_body or "<!doctype html" in low_body:
            html_features = _extract_html_features(body_text)
    reflection_markers = _extract_marker_hits(body_text, marker_candidates=marker_candidates)
    return ResponseFeatures(
        status_code=normalized.status_code,
        elapsed_ms=normalized.elapsed_ms,
        content_type_mime=mime,
        body_length=normalized.body_length,
        header_count=len(normalized.headers),
        redirect_target=redirect_target,
        header_name_set=set(normalized.headers.keys()),
        security_headers=security_headers,
        normalized_body_hash=normalized.body_normalized_hash,
        raw_body_hash=normalized.body_raw_hash,
        token_set=set(normalized.token_set),
        line_count=normalized.body_line_count,
        keyword_counts=keyword_counts,
        exception_names=exceptions,
        error_categories=error_categories,
        reflection_markers=reflection_markers,
        json_features=json_features,
        html_features=html_features,
        xml_features=xml_features,
        text_features=text_features,
    )

