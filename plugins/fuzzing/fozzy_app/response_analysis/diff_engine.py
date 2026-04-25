from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from .normalizer import seems_login_like
from .schemas import BodyDiffStats, DiffResult, HeaderDiff, ResponseFeatures


def _token_jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union <= 0:
        return 0.0
    return inter / union


def _normalized_similarity(text_a: str, text_b: str) -> float:
    if text_a == text_b:
        return 1.0
    if not text_a and not text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0
    a = text_a[:120000]
    b = text_b[:120000]
    return SequenceMatcher(None, a, b).ratio()


def _header_diff(
    baseline_headers: dict[str, str],
    fuzz_headers: dict[str, str],
    baseline_features: ResponseFeatures,
    fuzz_features: ResponseFeatures,
    *,
    baseline_content_type_raw: str,
    fuzz_content_type_raw: str,
    baseline_cache_control: set[str],
    fuzz_cache_control: set[str],
    baseline_set_cookie_semantics: list[dict[str, Any]],
    fuzz_set_cookie_semantics: list[dict[str, Any]],
    baseline_location: str,
    fuzz_location: str,
) -> HeaderDiff:
    base_keys = set(baseline_headers.keys())
    fuzz_keys = set(fuzz_headers.keys())
    new_headers = sorted(fuzz_keys - base_keys)
    missing_headers = sorted(base_keys - fuzz_keys)
    changed_headers = sorted(
        key for key in (base_keys & fuzz_keys) if str(baseline_headers.get(key, "")) != str(fuzz_headers.get(key, ""))
    )
    missing_security = sorted(
        key for key, present in baseline_features.security_headers.items() if present and not fuzz_features.security_headers.get(key, False)
    )
    semantic_changes: list[str] = []
    if baseline_features.content_type_mime != fuzz_features.content_type_mime:
        semantic_changes.append(
            f"content_type_mime:{baseline_features.content_type_mime}->{fuzz_features.content_type_mime}"
        )
    if baseline_content_type_raw != fuzz_content_type_raw:
        semantic_changes.append("content_type_charset_or_params_changed")
    if baseline_cache_control != fuzz_cache_control:
        semantic_changes.append("cache_control_semantics_changed")
    base_cookie_names = {(item.get("name", ""), tuple(item.get("attrs", []))) for item in baseline_set_cookie_semantics}
    fuzz_cookie_names = {(item.get("name", ""), tuple(item.get("attrs", []))) for item in fuzz_set_cookie_semantics}
    if base_cookie_names != fuzz_cookie_names:
        semantic_changes.append("set_cookie_semantics_changed")
    if baseline_location != fuzz_location:
        semantic_changes.append("location_semantics_changed")
    return HeaderDiff(
        new_headers=new_headers,
        missing_headers=missing_headers,
        changed_headers=changed_headers,
        missing_security_headers=missing_security,
        semantic_changes=semantic_changes,
    )


def build_response_diff(
    *,
    baseline_features: ResponseFeatures,
    fuzz_features: ResponseFeatures,
    baseline_body_normalized: str,
    fuzz_body_normalized: str,
    baseline_headers_comparable: dict[str, str],
    fuzz_headers_comparable: dict[str, str],
    baseline_content_type_raw: str,
    fuzz_content_type_raw: str,
    baseline_cache_control: set[str],
    fuzz_cache_control: set[str],
    baseline_set_cookie_semantics: list[dict[str, Any]],
    fuzz_set_cookie_semantics: list[dict[str, Any]],
    baseline_location: str,
    fuzz_location: str,
) -> DiffResult:
    similarity = _normalized_similarity(baseline_body_normalized, fuzz_body_normalized)
    jacc = _token_jaccard(baseline_features.token_set, fuzz_features.token_set)
    body_stats = BodyDiffStats(
        normalized_similarity=similarity,
        token_jaccard=jacc,
        baseline_length=baseline_features.body_length,
        fuzzed_length=fuzz_features.body_length,
        length_delta=fuzz_features.body_length - baseline_features.body_length,
        content_type_changed=baseline_features.content_type_mime != fuzz_features.content_type_mime,
        structure_changed=False,
    )
    json_schema_changed = False
    html_structure_changed = False
    if baseline_features.json_features.get("parse_ok") and fuzz_features.json_features.get("parse_ok"):
        base_paths = set(baseline_features.json_features.get("key_paths", []))
        fuzz_paths = set(fuzz_features.json_features.get("key_paths", []))
        json_schema_changed = base_paths != fuzz_paths
        body_stats.structure_changed = body_stats.structure_changed or json_schema_changed
    if baseline_features.html_features or fuzz_features.html_features:
        base_title = str((baseline_features.html_features or {}).get("title", "") or "")
        fuzz_title = str((fuzz_features.html_features or {}).get("title", "") or "")
        base_tags = (baseline_features.html_features or {}).get("tag_counts", {})
        fuzz_tags = (fuzz_features.html_features or {}).get("tag_counts", {})
        html_structure_changed = base_title != fuzz_title or base_tags != fuzz_tags
        body_stats.structure_changed = body_stats.structure_changed or html_structure_changed
    header_diff = _header_diff(
        baseline_headers_comparable,
        fuzz_headers_comparable,
        baseline_features,
        fuzz_features,
        baseline_content_type_raw=baseline_content_type_raw,
        fuzz_content_type_raw=fuzz_content_type_raw,
        baseline_cache_control=baseline_cache_control,
        fuzz_cache_control=fuzz_cache_control,
        baseline_set_cookie_semantics=baseline_set_cookie_semantics,
        fuzz_set_cookie_semantics=fuzz_set_cookie_semantics,
        baseline_location=baseline_location,
        fuzz_location=fuzz_location,
    )
    redirect_changed = baseline_features.redirect_target != fuzz_features.redirect_target
    auth_behavior_changed = False
    auth_reason = ""
    if redirect_changed and seems_login_like(fuzz_features.redirect_target):
        auth_behavior_changed = True
        auth_reason = "redirect_to_login_like_route"
    if not auth_behavior_changed and (fuzz_features.html_features or {}).get("forms", 0) > (baseline_features.html_features or {}).get("forms", 0):
        if seems_login_like(fuzz_body_normalized):
            auth_behavior_changed = True
            auth_reason = "login_form_appeared"
    status_changed = baseline_features.status_code != fuzz_features.status_code
    content_type_changed = baseline_features.content_type_mime != fuzz_features.content_type_mime
    only_volatile_like = (
        not status_changed
        and not header_diff.semantic_changes
        and not header_diff.new_headers
        and not header_diff.missing_headers
        and similarity >= 0.985
    )
    return DiffResult(
        status_changed=status_changed,
        status_from=baseline_features.status_code,
        status_to=fuzz_features.status_code,
        header_diff=header_diff,
        body_diff_stats=body_stats,
        redirect_changed=redirect_changed,
        content_type_changed=content_type_changed,
        json_schema_changed=json_schema_changed,
        html_structure_changed=html_structure_changed,
        similarity=similarity,
        noisy_only=only_volatile_like,
        auth_behavior_changed=auth_behavior_changed,
        auth_change_reason=auth_reason,
    )

