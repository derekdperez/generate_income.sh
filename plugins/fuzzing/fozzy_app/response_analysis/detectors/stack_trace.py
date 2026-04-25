from __future__ import annotations

from ..pattern_library import JAVA_CAUSED_BY_RE, JAVA_EXCEPTION_RE, JAVA_STACK_LINE_RE, STACK_FRAMEWORK_RE
from ..schemas import Finding
from .base import DetectorContext


class StackTraceDetector:
    detector_id = "stack_trace_detector"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        text = ctx.fuzz_body_raw
        stack_lines = JAVA_STACK_LINE_RE.findall(text)
        caused_by = JAVA_CAUSED_BY_RE.findall(text)
        exceptions = JAVA_EXCEPTION_RE.findall(text)
        framework_hits = STACK_FRAMEWORK_RE.findall(text)
        if not stack_lines and not caused_by and not exceptions:
            return []
        evidence = {
            "stack_line_count": len(stack_lines),
            "caused_by": sorted(set(caused_by))[:8],
            "exception_names": sorted(set(exceptions))[:12],
            "framework_hits": sorted(set(framework_hits))[:8],
        }
        return [
            Finding(
                id="java_stack_trace",
                title="Java stack trace or exception markers detected",
                category="stack_trace",
                severity="critical",
                score_contribution=34,
                confidence=0.96,
                evidence=evidence,
                description="Response body contains Java stack frames and/or exception class names.",
            )
        ]

