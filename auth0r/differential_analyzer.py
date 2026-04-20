
from __future__ import annotations

from difflib import SequenceMatcher
import json
from typing import Any


SENSITIVE_FIELD_HINTS = {
    "email", "username", "user", "full_name", "name", "role", "tenant", "account_id",
    "token", "session", "apikey", "api_key", "secret", "phone", "address",
}


def body_similarity(a: str, b: str) -> float:
    return float(SequenceMatcher(None, a or "", b or "").ratio())


def _json_load(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _json_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_json_shape(value[0])] if value else []
    return type(value).__name__


def _extract_sensitive_keys(value: Any) -> list[str]:
    hits: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in SENSITIVE_FIELD_HINTS or any(hint in lowered for hint in SENSITIVE_FIELD_HINTS):
                hits.add(str(key))
            hits.update(_extract_sensitive_keys(nested))
    elif isinstance(value, list):
        for item in value[:10]:
            hits.update(_extract_sensitive_keys(item))
    return sorted(hits)


def compare_responses(
    baseline,
    candidate,
    *,
    authenticated_hits: list[str],
    denial_hits: list[str],
    baseline_authenticated_hits: list[str] | None = None,
) -> dict:
    baseline_text = baseline.text if baseline is not None else ""
    candidate_text = candidate.text if candidate is not None else ""
    similarity = body_similarity(baseline_text, candidate_text)
    baseline_json = _json_load(baseline_text)
    candidate_json = _json_load(candidate_text)
    baseline_shape = _json_shape(baseline_json) if baseline_json is not None else None
    candidate_shape = _json_shape(candidate_json) if candidate_json is not None else None
    json_shape_match = baseline_shape is not None and candidate_shape is not None and baseline_shape == candidate_shape
    redirect_chain_match = (
        baseline is not None and candidate is not None and
        [str(r.headers.get("location", "")) for r in baseline.history] ==
        [str(r.headers.get("location", "")) for r in candidate.history]
    )
    baseline_sensitive = _extract_sensitive_keys(baseline_json) if baseline_json is not None else []
    candidate_sensitive = _extract_sensitive_keys(candidate_json) if candidate_json is not None else []
    candidate_exposes_sensitive = bool(set(candidate_sensitive) & set(baseline_sensitive))
    same_status = (
        candidate is not None and baseline is not None and
        getattr(baseline, "status_code", None) == getattr(candidate, "status_code", None)
    )
    candidate_authenticated = bool(authenticated_hits)
    baseline_authenticated = bool(baseline_authenticated_hits or [])
    suspicious = bool(
        candidate is not None
        and baseline is not None
        and same_status
        and (
            similarity >= 0.90
            or json_shape_match
            or redirect_chain_match
            or candidate_exposes_sensitive
            or (candidate_authenticated and baseline_authenticated)
        )
        and not denial_hits
    )
    return {
        "baseline_status_code": getattr(baseline, "status_code", None),
        "candidate_status_code": getattr(candidate, "status_code", None),
        "body_similarity": similarity,
        "baseline_length": len(baseline_text),
        "candidate_length": len(candidate_text),
        "baseline_authenticated_markers_matched": list(baseline_authenticated_hits or []),
        "authenticated_markers_matched": list(authenticated_hits or []),
        "denial_markers_matched": list(denial_hits or []),
        "json_shape_match": json_shape_match,
        "redirect_chain_match": redirect_chain_match,
        "baseline_sensitive_keys": baseline_sensitive,
        "candidate_sensitive_keys": candidate_sensitive,
        "candidate_exposes_sensitive_fields": candidate_exposes_sensitive,
        "suspicious": suspicious,
    }
