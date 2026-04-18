from __future__ import annotations

from ..schemas import Finding
from .base import DetectorContext


class HeaderSemanticChangeDetector:
    detector_id = "header_semantic_change_detector"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        findings: list[Finding] = []
        sem = list(ctx.diff.header_diff.semantic_changes)
        if not sem:
            return findings
        score = 0
        severity = "low"
        if ctx.diff.content_type_changed:
            score += 18
            severity = "high"
        if ctx.diff.redirect_changed:
            score += 14
            severity = "high"
        if "set_cookie_semantics_changed" in sem:
            score += 10
            severity = "medium" if severity == "low" else severity
        if "cache_control_semantics_changed" in sem:
            score += 6
        findings.append(
            Finding(
                id="header_semantic_change",
                title="Header semantics changed",
                category="header_semantic",
                severity=severity,
                score_contribution=max(8, score),
                confidence=0.9,
                evidence={"semantic_changes": sem},
                description="Semantically meaningful header behavior changed relative to baseline.",
            )
        )
        return findings

