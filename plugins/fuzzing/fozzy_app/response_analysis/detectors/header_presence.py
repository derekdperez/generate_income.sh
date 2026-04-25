from __future__ import annotations

from ..pattern_library import DEBUG_HEADER_HINTS
from ..schemas import Finding
from .base import DetectorContext


class HeaderPresenceDiffDetector:
    detector_id = "header_presence_diff_detector"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        findings: list[Finding] = []
        hd = ctx.diff.header_diff
        if hd.new_headers:
            debug_headers = [item for item in hd.new_headers if item in DEBUG_HEADER_HINTS]
            score = 8 + (8 if debug_headers else 0)
            severity = "medium" if debug_headers else "low"
            findings.append(
                Finding(
                    id="new_headers",
                    title=f"New response headers detected ({len(hd.new_headers)})",
                    category="header_presence",
                    severity=severity,
                    score_contribution=score,
                    confidence=0.85,
                    evidence={"new_headers": hd.new_headers, "debug_like_headers": debug_headers},
                    description="Headers present in fuzzed response but absent from baseline.",
                )
            )
        if hd.missing_headers:
            findings.append(
                Finding(
                    id="missing_headers",
                    title=f"Response headers missing vs baseline ({len(hd.missing_headers)})",
                    category="header_presence",
                    severity="low",
                    score_contribution=6,
                    confidence=0.82,
                    evidence={"missing_headers": hd.missing_headers},
                    description="Headers were removed relative to baseline response.",
                )
            )
        if hd.missing_security_headers:
            findings.append(
                Finding(
                    id="missing_security_headers",
                    title=f"Security headers disappeared ({len(hd.missing_security_headers)})",
                    category="security_headers",
                    severity="high",
                    score_contribution=20,
                    confidence=0.92,
                    evidence={"missing_security_headers": hd.missing_security_headers},
                    description="Security headers present in baseline were not present in fuzzed response.",
                )
            )
        return findings

