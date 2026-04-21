from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, set):
        return sorted(_json_safe(v) for v in value)
    return value


@dataclass
class Finding:
    id: str
    title: str
    category: str
    severity: str
    score_contribution: int
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confidence"] = round(float(self.confidence), 4)
        payload["evidence"] = _json_safe(payload.get("evidence", {}))
        return payload


@dataclass
class HeaderDiff:
    new_headers: list[str] = field(default_factory=list)
    missing_headers: list[str] = field(default_factory=list)
    changed_headers: list[str] = field(default_factory=list)
    missing_security_headers: list[str] = field(default_factory=list)
    semantic_changes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class BodyDiffStats:
    normalized_similarity: float = 1.0
    token_jaccard: float = 1.0
    baseline_length: int = 0
    fuzzed_length: int = 0
    length_delta: int = 0
    content_type_changed: bool = False
    structure_changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["normalized_similarity"] = round(float(self.normalized_similarity), 5)
        payload["token_jaccard"] = round(float(self.token_jaccard), 5)
        return payload


@dataclass
class AnalysisOutput:
    request_id: str
    baseline_id: str
    cluster_id: str
    cluster_label: str
    normalized_signature: str
    status: str
    summary: str
    score: int
    findings: list[Finding] = field(default_factory=list)
    header_diff: HeaderDiff = field(default_factory=HeaderDiff)
    body_diff_stats: BodyDiffStats = field(default_factory=BodyDiffStats)
    similarity: float = 1.0
    reflection: list[dict[str, Any]] = field(default_factory=list)
    extracted_exceptions: list[str] = field(default_factory=list)
    error_categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    cluster_occurrence: int = 1
    baseline_profile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": str(self.request_id),
            "baseline_id": str(self.baseline_id),
            "cluster_id": str(self.cluster_id),
            "cluster_label": str(self.cluster_label),
            "normalized_signature": str(self.normalized_signature),
            "status": str(self.status),
            "summary": str(self.summary),
            "score": int(self.score),
            "findings": [item.to_dict() for item in self.findings],
            "header_diff": self.header_diff.to_dict(),
            "body_diff_stats": self.body_diff_stats.to_dict(),
            "similarity": round(float(self.similarity), 5),
            "reflection": _json_safe(self.reflection),
            "extracted_exceptions": sorted(set(str(v) for v in self.extracted_exceptions if str(v))),
            "error_categories": sorted(set(str(v) for v in self.error_categories if str(v))),
            "tags": sorted(set(str(v) for v in self.tags if str(v))),
            "cluster_occurrence": int(self.cluster_occurrence),
            "baseline_profile": _json_safe(self.baseline_profile),
        }


@dataclass
class NormalizedResponse:
    status_code: int
    elapsed_ms: int
    url: str
    content_type_raw: str
    content_type_mime: str
    content_type_charset: str
    headers: dict[str, list[str]]
    comparable_headers: dict[str, str]
    cache_control_directives: set[str]
    set_cookie_semantics: list[dict[str, Any]]
    location_normalized: str
    body_raw_text: str
    body_normalized_text: str
    body_raw_hash: str
    body_normalized_hash: str
    body_length: int
    body_line_count: int
    token_set: set[str]


@dataclass
class ResponseFeatures:
    status_code: int
    elapsed_ms: int
    content_type_mime: str
    body_length: int
    header_count: int
    redirect_target: str
    header_name_set: set[str]
    security_headers: dict[str, bool]
    normalized_body_hash: str
    raw_body_hash: str
    token_set: set[str]
    line_count: int
    keyword_counts: dict[str, int]
    exception_names: list[str]
    error_categories: list[str]
    reflection_markers: list[dict[str, Any]]
    json_features: dict[str, Any]
    html_features: dict[str, Any]
    xml_features: dict[str, Any]
    text_features: dict[str, Any]


@dataclass
class DiffResult:
    status_changed: bool
    status_from: int
    status_to: int
    header_diff: HeaderDiff
    body_diff_stats: BodyDiffStats
    redirect_changed: bool
    content_type_changed: bool
    json_schema_changed: bool
    html_structure_changed: bool
    similarity: float
    noisy_only: bool
    auth_behavior_changed: bool
    auth_change_reason: str


@dataclass
class BaselineProfile:
    baseline_id: str
    template_key: str
    method: str
    route_pattern: str
    content_type_mime: str
    parameter_layout: list[str]
    status_code: int
    redirect_pattern: str
    response_size_min: int
    response_size_max: int
    response_time_min: int
    response_time_max: int
    body_fingerprint: str
    body_structure_fingerprint: str
    common_body_keywords: list[str]
    header_signature: list[str]
    sample_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

