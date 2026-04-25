from __future__ import annotations

from ..schemas import Finding
from .base import DetectorContext


class StatusChangeDetector:
    detector_id = "status_change_detector"

    @staticmethod
    def _class(code: int) -> str:
        if code >= 500:
            return "5xx"
        if code >= 400:
            return "4xx"
        if code >= 300:
            return "3xx"
        if code >= 200:
            return "2xx"
        return "other"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        if not ctx.diff.status_changed:
            return []
        old = int(ctx.diff.status_from)
        new = int(ctx.diff.status_to)
        old_cls = self._class(old)
        new_cls = self._class(new)
        severity = "medium"
        score = 10
        if old_cls == "2xx" and new_cls == "5xx":
            severity, score = "critical", 35
        elif old_cls == "2xx" and new_cls == "4xx":
            severity, score = "high", 22
        elif old == 403 and new == 200:
            severity, score = "high", 24
        elif old_cls == "4xx" and new_cls == "5xx":
            severity, score = "high", 18
        elif old_cls == "3xx" and new_cls == "2xx":
            severity, score = "medium", 12
        return [
            Finding(
                id="status_change",
                title=f"HTTP status changed {old} -> {new}",
                category="status",
                severity=severity,
                score_contribution=score,
                confidence=0.98,
                evidence={"from": old, "to": new, "from_class": old_cls, "to_class": new_cls},
                description="Baseline and fuzzed responses returned different HTTP status classes.",
            )
        ]

