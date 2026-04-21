from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..schemas import DiffResult, Finding, ResponseFeatures


@dataclass
class DetectorContext:
    request_context: dict[str, Any]
    baseline_features: ResponseFeatures
    fuzz_features: ResponseFeatures
    diff: DiffResult
    baseline_body_raw: str
    fuzz_body_raw: str
    baseline_body_normalized: str
    fuzz_body_normalized: str
    baseline_headers_raw: dict[str, list[str]]
    fuzz_headers_raw: dict[str, list[str]]
    baseline_location: str
    fuzz_location: str
    marker_candidates: list[str]


class Detector(Protocol):
    detector_id: str

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        ...

