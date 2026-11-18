"""Validate native fixture outputs against this worker release's PartMetadata v1 contract."""

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


@lru_cache(maxsize=1)
def validator():
    path = Path(__file__).resolve().parents[1] / "contracts" / "part_metadata_v1.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_part_metadata(metadata):
    validator().validate(metadata)
    return metadata
