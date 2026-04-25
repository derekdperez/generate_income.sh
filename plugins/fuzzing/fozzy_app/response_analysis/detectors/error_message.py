from __future__ import annotations

from ..pattern_library import PROXY_ERROR_RE, SPRING_WHITELABEL_RE, SQL_ERROR_RE, TOMCAT_JETTY_RE
from ..schemas import Finding
from .base import DetectorContext


class ErrorMessageDetector:
    detector_id = "error_message_detector"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        categories = list(ctx.fuzz_features.error_categories)
        text = ctx.fuzz_body_raw
        if SQL_ERROR_RE.search(text) and "sql_database_error" not in categories:
            categories.append("sql_database_error")
        if SPRING_WHITELABEL_RE.search(text):
            categories.append("server_container_error")
        if TOMCAT_JETTY_RE.search(text):
            categories.append("server_container_error")
        if PROXY_ERROR_RE.search(text):
            categories.append("proxy_upstream_error")
        categories = sorted(set(categories))
        if not categories:
            return []
        score = 0
        if "sql_database_error" in categories:
            score += 20
        if "java_exception" in categories:
            score += 20
        if "server_container_error" in categories:
            score += 15
        if "proxy_upstream_error" in categories:
            score += 12
        if score == 0:
            score = 10
        severity = "high" if score >= 16 else "medium"
        return [
            Finding(
                id="error_message",
                title="Error-like response content detected",
                category="error_content",
                severity=severity,
                score_contribution=score,
                confidence=0.86,
                evidence={"categories": categories, "keyword_counts": ctx.fuzz_features.keyword_counts},
                description="Keyword and pattern scoring indicates application/server error behavior.",
            )
        ]

