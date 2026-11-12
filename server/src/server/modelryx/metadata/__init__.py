"""CAD-neutral engineering metadata validation and ingestion."""

from modelryx.metadata.derived import build_derived_metadata
from modelryx.metadata.engineering_validator import (
    EngineeringMetadataError,
    validate_engineering_metadata,
)
from modelryx.metadata.equivalence import EquivalenceDifference, compare_part_metadata
from modelryx.metadata.service import MetadataService
from modelryx.metadata.validator import MetadataSchemaError, validate_part_metadata

__all__ = [
    "EngineeringMetadataError",
    "EquivalenceDifference",
    "MetadataSchemaError",
    "MetadataService",
    "build_derived_metadata",
    "compare_part_metadata",
    "validate_engineering_metadata",
    "validate_part_metadata",
]
