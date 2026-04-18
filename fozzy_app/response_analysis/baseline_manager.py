from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from .schemas import BaselineProfile, ResponseFeatures


def _route_pattern(path: str) -> str:
    raw = str(path or "/")
    # Normalize path segments to avoid over-fragmented baselines.
    raw = re.sub(r"/\d{2,}(?=/|$)", "/{num}", raw)
    raw = re.sub(
        r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}(?=/|$)",
        "/{uuid}",
        raw,
    )
    raw = re.sub(r"/[0-9a-fA-F]{16,}(?=/|$)", "/{hex}", raw)
    return raw or "/"


def _body_structure_fingerprint(features: ResponseFeatures) -> str:
    if features.json_features.get("key_paths"):
        src = "json:" + "|".join(features.json_features.get("key_paths", [])[:100])
        return hashlib.sha1(src.encode("utf-8")).hexdigest()
    if features.html_features.get("tag_counts"):
        tag_counts = features.html_features.get("tag_counts", {})
        src = "html:" + "|".join(f"{k}:{tag_counts[k]}" for k in sorted(tag_counts.keys())[:120])
        return hashlib.sha1(src.encode("utf-8")).hexdigest()
    if features.xml_features.get("path_signature"):
        src = "xml:" + "|".join(features.xml_features.get("path_signature", [])[:120])
        return hashlib.sha1(src.encode("utf-8")).hexdigest()
    src = "txt:" + "|".join(sorted(list(features.token_set))[:120])
    return hashlib.sha1(src.encode("utf-8")).hexdigest()


class BaselineManager:
    def __init__(self) -> None:
        self._profiles: dict[str, BaselineProfile] = {}

    @staticmethod
    def template_key_from_context(context: dict[str, Any], features: ResponseFeatures) -> str:
        method = str(context.get("http_method", "GET") or "GET").strip().upper()
        request_url = str(context.get("request_url", "") or "")
        parsed = urlparse(request_url)
        path_pattern = _route_pattern(parsed.path or str(context.get("path", "/") or "/"))
        layout = context.get("parameter_layout", [])
        layout_items = sorted(str(item or "") for item in layout if str(item or ""))
        layout_key = ",".join(layout_items) if layout_items else "-"
        mime = str(features.content_type_mime or "").strip().lower() or "-"
        return f"{method}|{path_pattern}|{mime}|{layout_key}"

    def upsert_baseline(self, context: dict[str, Any], features: ResponseFeatures) -> BaselineProfile:
        key = self.template_key_from_context(context, features)
        existing = self._profiles.get(key)
        method = str(context.get("http_method", "GET") or "GET").strip().upper()
        request_url = str(context.get("request_url", "") or "")
        parsed = urlparse(request_url)
        route_pattern = _route_pattern(parsed.path or str(context.get("path", "/") or "/"))
        parameter_layout = sorted(str(item or "") for item in context.get("parameter_layout", []) if str(item or ""))
        common_body_keywords = sorted(list(features.token_set))[:40]
        header_signature = sorted(list(features.header_name_set))
        if existing is None:
            baseline_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
            created = BaselineProfile(
                baseline_id=baseline_id,
                template_key=key,
                method=method,
                route_pattern=route_pattern,
                content_type_mime=features.content_type_mime,
                parameter_layout=parameter_layout,
                status_code=features.status_code,
                redirect_pattern=features.redirect_target,
                response_size_min=features.body_length,
                response_size_max=features.body_length,
                response_time_min=features.elapsed_ms,
                response_time_max=features.elapsed_ms,
                body_fingerprint=features.normalized_body_hash,
                body_structure_fingerprint=_body_structure_fingerprint(features),
                common_body_keywords=common_body_keywords,
                header_signature=header_signature,
                sample_count=1,
            )
            self._profiles[key] = created
            return created
        existing.sample_count += 1
        existing.status_code = features.status_code
        existing.redirect_pattern = features.redirect_target
        existing.content_type_mime = features.content_type_mime
        existing.response_size_min = min(existing.response_size_min, features.body_length)
        existing.response_size_max = max(existing.response_size_max, features.body_length)
        existing.response_time_min = min(existing.response_time_min, features.elapsed_ms)
        existing.response_time_max = max(existing.response_time_max, features.elapsed_ms)
        existing.body_fingerprint = features.normalized_body_hash
        existing.body_structure_fingerprint = _body_structure_fingerprint(features)
        # Keep stable high-signal overlap keywords.
        merged = set(existing.common_body_keywords) & set(common_body_keywords)
        if not merged:
            merged = set(common_body_keywords)
        existing.common_body_keywords = sorted(merged)[:40]
        existing.header_signature = sorted(set(existing.header_signature) | set(header_signature))
        return existing

