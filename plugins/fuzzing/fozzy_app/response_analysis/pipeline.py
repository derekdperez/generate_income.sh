from __future__ import annotations

from typing import Any

from .baseline_manager import BaselineManager
from .clusterer import ResponseClusterer
from .detectors import DetectorContext, default_detectors
from .diff_engine import build_response_diff
from .feature_extractor import extract_features
from .normalizer import extract_marker_candidates, normalize_response
from .scorer import score_findings, score_to_status
from .schemas import AnalysisOutput, Finding
from .summarizer import build_summary


class ResponseAnalysisPipeline:
    """Deterministic baseline-driven fuzz response analyzer."""

    def __init__(self) -> None:
        self._baseline_manager = BaselineManager()
        self._clusterer = ResponseClusterer()
        self._detectors = default_detectors()

    def _normalize_request_context(self, request_context: dict[str, Any]) -> dict[str, Any]:
        ctx = dict(request_context or {})
        method = str(ctx.get("http_method", "GET") or "GET").strip().upper() or "GET"
        request_url = str(ctx.get("request_url") or ctx.get("url") or "").strip()
        parameter_layout = ctx.get("parameter_layout", [])
        if not isinstance(parameter_layout, list):
            parameter_layout = []
        return {
            "request_id": str(ctx.get("request_id", "") or ""),
            "http_method": method,
            "request_url": request_url,
            "path": str(ctx.get("path", "") or ""),
            "host": str(ctx.get("host", "") or ""),
            "parameter_layout": [str(item or "") for item in parameter_layout if str(item or "")],
            "mutated_parameter": str(ctx.get("mutated_parameter", "") or ""),
            "mutated_value": str(ctx.get("mutated_value", "") or ""),
        }

    def _dedupe_findings(self, findings: list[Finding]) -> list[Finding]:
        seen: set[tuple[str, str]] = set()
        out: list[Finding] = []
        for finding in findings:
            key = (str(finding.id), str(finding.title))
            if key in seen:
                continue
            seen.add(key)
            out.append(finding)
        return out

    def _apply_noise_suppression(
        self,
        *,
        findings: list[Finding],
        similarity: float,
        noisy_only: bool,
        cluster_occurrence: int,
    ) -> tuple[list[Finding], list[str]]:
        tags: list[str] = []
        out = list(findings)
        if noisy_only or similarity >= 0.99:
            out = [
                item
                for item in out
                if item.severity in {"medium", "high", "critical"} or int(item.score_contribution) >= 12
            ]
            if not out:
                tags.append("noise_suppressed")
        if cluster_occurrence >= 5 and all(item.severity in {"low", "medium"} for item in out):
            out = out[:1]
            tags.append("repeated_cluster_suppressed")
        return out, tags

    def analyze_response(
        self,
        *,
        request_context: dict[str, Any],
        baseline_response: dict[str, Any],
        fuzzed_response: dict[str, Any],
    ) -> dict[str, Any]:
        ctx = self._normalize_request_context(request_context)
        marker_candidates = extract_marker_candidates(ctx.get("mutated_value", ""))

        baseline_norm = normalize_response(baseline_response, request_url=ctx.get("request_url", ""))
        fuzz_norm = normalize_response(fuzzed_response, request_url=ctx.get("request_url", ""))
        baseline_features = extract_features(baseline_norm, marker_candidates=marker_candidates)
        fuzz_features = extract_features(fuzz_norm, marker_candidates=marker_candidates)

        baseline_profile = self._baseline_manager.upsert_baseline(ctx, baseline_features)
        diff = build_response_diff(
            baseline_features=baseline_features,
            fuzz_features=fuzz_features,
            baseline_body_normalized=baseline_norm.body_normalized_text,
            fuzz_body_normalized=fuzz_norm.body_normalized_text,
            baseline_headers_comparable=baseline_norm.comparable_headers,
            fuzz_headers_comparable=fuzz_norm.comparable_headers,
            baseline_content_type_raw=baseline_norm.content_type_raw,
            fuzz_content_type_raw=fuzz_norm.content_type_raw,
            baseline_cache_control=baseline_norm.cache_control_directives,
            fuzz_cache_control=fuzz_norm.cache_control_directives,
            baseline_set_cookie_semantics=baseline_norm.set_cookie_semantics,
            fuzz_set_cookie_semantics=fuzz_norm.set_cookie_semantics,
            baseline_location=baseline_norm.location_normalized,
            fuzz_location=fuzz_norm.location_normalized,
        )

        detector_ctx = DetectorContext(
            request_context=ctx,
            baseline_features=baseline_features,
            fuzz_features=fuzz_features,
            diff=diff,
            baseline_body_raw=baseline_norm.body_raw_text,
            fuzz_body_raw=fuzz_norm.body_raw_text,
            baseline_body_normalized=baseline_norm.body_normalized_text,
            fuzz_body_normalized=fuzz_norm.body_normalized_text,
            baseline_headers_raw=baseline_norm.headers,
            fuzz_headers_raw=fuzz_norm.headers,
            baseline_location=baseline_norm.location_normalized,
            fuzz_location=fuzz_norm.location_normalized,
            marker_candidates=marker_candidates,
        )

        findings: list[Finding] = []
        for detector in self._detectors:
            findings.extend(detector.detect(detector_ctx))
        findings = self._dedupe_findings(findings)

        signature = self._clusterer.signature_for(
            status_code=fuzz_features.status_code,
            normalized_body_hash=fuzz_features.normalized_body_hash,
            finding_ids=[item.id for item in findings],
            exception_names=fuzz_features.exception_names,
            content_type_mime=fuzz_features.content_type_mime,
        )
        cluster = self._clusterer.assign(
            signature=signature,
            status_code=fuzz_features.status_code,
            findings=findings,
            exceptions=fuzz_features.exception_names,
        )

        findings, suppression_tags = self._apply_noise_suppression(
            findings=findings,
            similarity=diff.similarity,
            noisy_only=diff.noisy_only,
            cluster_occurrence=cluster.occurrence,
        )
        score = score_findings(findings, similarity=diff.similarity, noisy_only=diff.noisy_only)
        status = score_to_status(score, findings)
        summary = build_summary(diff=diff, findings=findings)

        reflection: list[dict[str, Any]] = []
        for finding in findings:
            if finding.id == "reflection_detected":
                matches = finding.evidence.get("matches", [])
                if isinstance(matches, list):
                    for item in matches:
                        if isinstance(item, dict):
                            reflection.append(
                                {
                                    "marker": str(item.get("marker", "") or ""),
                                    "contexts": str(item.get("contexts", "") or "").split(","),
                                }
                            )
        tags = set()
        tags.add(cluster.label)
        tags.update(suppression_tags)
        if diff.content_type_changed:
            tags.add("content_type_changed")
        if diff.redirect_changed:
            tags.add("redirect_changed")
        if diff.auth_behavior_changed:
            tags.add("auth_behavior_changed")
        if fuzz_features.exception_names:
            tags.add("exception_present")

        output = AnalysisOutput(
            request_id=ctx.get("request_id", "") or "",
            baseline_id=baseline_profile.baseline_id,
            cluster_id=cluster.cluster_id,
            cluster_label=cluster.label,
            normalized_signature=signature,
            status=status,
            summary=summary,
            score=score,
            findings=findings,
            header_diff=diff.header_diff,
            body_diff_stats=diff.body_diff_stats,
            similarity=diff.similarity,
            reflection=reflection,
            extracted_exceptions=list(fuzz_features.exception_names),
            error_categories=list(fuzz_features.error_categories),
            tags=sorted(tags),
            cluster_occurrence=cluster.occurrence,
            baseline_profile=baseline_profile.to_dict(),
        )
        return output.to_dict()
