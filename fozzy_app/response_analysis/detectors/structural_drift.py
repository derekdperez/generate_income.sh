from __future__ import annotations

from ..normalizer import seems_login_like
from ..schemas import Finding
from .base import DetectorContext


class StructuralDriftDetector:
    detector_id = "structural_drift_detector"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        findings: list[Finding] = []
        reasons: list[str] = []
        score = 0
        if ctx.diff.content_type_changed:
            reasons.append("content_type_changed")
            score += 15
        if ctx.diff.json_schema_changed:
            reasons.append("json_schema_changed")
            score += 12
        if ctx.diff.html_structure_changed:
            reasons.append("html_structure_changed")
            score += 12
        base_title = str((ctx.baseline_features.html_features or {}).get("title", "") or "")
        fuzz_title = str((ctx.fuzz_features.html_features or {}).get("title", "") or "")
        if base_title != fuzz_title and (base_title or fuzz_title):
            reasons.append("html_title_changed")
            score += 8
        base_forms = int((ctx.baseline_features.html_features or {}).get("forms", 0) or 0)
        fuzz_forms = int((ctx.fuzz_features.html_features or {}).get("forms", 0) or 0)
        if fuzz_forms > base_forms and seems_login_like(ctx.fuzz_body_normalized):
            reasons.append("login_form_appeared")
            score += 14
        if ctx.diff.similarity < 0.55:
            reasons.append("major_body_similarity_drop")
            score += 10
        if not reasons:
            return findings
        severity = "high" if score >= 20 else "medium"
        findings.append(
            Finding(
                id="structural_drift",
                title="Response structure drift detected",
                category="structural_drift",
                severity=severity,
                score_contribution=max(8, score),
                confidence=0.84,
                evidence={"reasons": reasons, "similarity": round(ctx.diff.similarity, 5)},
                description="Response shape changed materially compared with the endpoint baseline.",
            )
        )
        return findings

