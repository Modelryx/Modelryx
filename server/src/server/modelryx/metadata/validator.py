"""JSON Schema validation for the PartMetadata worker/server contract."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


class MetadataSchemaError(ValueError):
    """Raised when a PartMetadata document violates its versioned contract."""


@lru_cache(maxsize=1)
def part_metadata_v1_validator() -> Draft202012Validator:
    resource = files("modelryx.metadata.schemas").joinpath("part_metadata_v1.json")
    schema = json.loads(resource.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_part_metadata(metadata: Any) -> dict[str, Any]:
    """Validate and return a PartMetadata v1 document without modifying it."""
    errors = sorted(
        part_metadata_v1_validator().iter_errors(metadata),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        raise MetadataSchemaError(f"PartMetadata schema error at {path}: {error.message}")
    return metadata
