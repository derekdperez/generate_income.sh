from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .schemas import Finding


@dataclass
class ClusterInfo:
    cluster_id: str
    label: str
    signature: str
    occurrence: int


class ResponseClusterer:
    def __init__(self) -> None:
        self._signature_to_cluster: dict[str, ClusterInfo] = {}

    def _label_from_inputs(self, *, status_code: int, findings: list[Finding], exceptions: list[str]) -> str:
        ids = {str(item.id) for item in findings}
        if "java_stack_trace" in ids or exceptions:
            return "java_exception"
        if "reflection_detected" in ids:
            return "reflection"
        if "missing_security_headers" in ids:
            return "security_header_regression"
        if "header_semantic_change" in ids and status_code in {301, 302, 303, 307, 308}:
            return "redirect_behavior_change"
        if status_code >= 500:
            return "server_error"
        if status_code in {401, 403}:
            return "access_denied"
        if status_code in {301, 302, 303, 307, 308}:
            return "redirect"
        if status_code >= 400:
            return "validation_or_client_error"
        if findings:
            return "interesting_drift"
        return "normal"

    def signature_for(
        self,
        *,
        status_code: int,
        normalized_body_hash: str,
        finding_ids: list[str],
        exception_names: list[str],
        content_type_mime: str,
    ) -> str:
        parts = [
            str(status_code),
            str(content_type_mime or ""),
            str(normalized_body_hash or ""),
            ",".join(sorted(set(str(item) for item in finding_ids if str(item)))),
            ",".join(sorted(set(str(item) for item in exception_names if str(item)))[:3]),
        ]
        return "|".join(parts)

    def assign(
        self,
        *,
        signature: str,
        status_code: int,
        findings: list[Finding],
        exceptions: list[str],
    ) -> ClusterInfo:
        existing = self._signature_to_cluster.get(signature)
        if existing is not None:
            existing.occurrence += 1
            return existing
        cluster_id = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
        label = self._label_from_inputs(status_code=status_code, findings=findings, exceptions=exceptions)
        info = ClusterInfo(cluster_id=cluster_id, label=label, signature=signature, occurrence=1)
        self._signature_to_cluster[signature] = info
        return info

