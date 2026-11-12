from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Classification(StrEnum):
    EXACT_FILE_DUPLICATE = "exact_file_duplicate"
    ENGINEERING_DUPLICATE = "engineering_duplicate"
    LIKELY_DUPLICATE = "likely_duplicate"
    VARIANT = "variant"
    SIMILAR = "similar"
    DIFFERENT = "different"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class ScoreBreakdown:
    geometry: float | None = None
    features: float | None = None
    physical: float | None = None
    material: float | None = None
    identity: float | None = None
    assembly: float | None = None
    metadata: float | None = None
    embedding: float | None = None
    combined: float | None = None


@dataclass(frozen=True)
class DuplicateResult:
    classification: Classification
    scores: ScoreBreakdown
    reasons: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)
    recommended_action: str = "continue_creation"
