from __future__ import annotations

from .base import Detector, DetectorContext
from .error_message import ErrorMessageDetector
from .header_presence import HeaderPresenceDiffDetector
from .header_semantic import HeaderSemanticChangeDetector
from .reflection import ReflectionDetector
from .stack_trace import StackTraceDetector
from .status_change import StatusChangeDetector
from .structural_drift import StructuralDriftDetector


def default_detectors() -> list[Detector]:
    return [
        StatusChangeDetector(),
        HeaderPresenceDiffDetector(),
        HeaderSemanticChangeDetector(),
        StackTraceDetector(),
        ErrorMessageDetector(),
        ReflectionDetector(),
        StructuralDriftDetector(),
    ]


__all__ = [
    "Detector",
    "DetectorContext",
    "default_detectors",
    "StatusChangeDetector",
    "HeaderPresenceDiffDetector",
    "HeaderSemanticChangeDetector",
    "StackTraceDetector",
    "ErrorMessageDetector",
    "ReflectionDetector",
    "StructuralDriftDetector",
]

