"""Provider-independent duplicate detection for normalized PartMetadata."""

from .hashing import canonical_package_sha256
from .models import Classification, DuplicateResult, ScoreBreakdown
from .service import DuplicateDetectionService

__all__ = [
    "Classification",
    "DuplicateDetectionService",
    "DuplicateResult",
    "ScoreBreakdown",
    "canonical_package_sha256",
]
