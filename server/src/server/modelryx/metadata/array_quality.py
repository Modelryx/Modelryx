"""Database-side guard for unreliable FreeCAD array measurements."""

from typing import Any

from sqlalchemy import cast, func, literal, or_
from sqlalchemy.dialects.postgresql import JSONPATH

_DIAGNOSTIC_PATH = (
    '$.native.extraction_diagnostics[*] ? ('
    '@.code == "LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN" || '
    '@.code == "LINK_ARRAY_EVIDENCE_TRUNCATED" || '
    '@.code == "LINK_ARRAY_EXTRACTION_FAILED" || '
    '@.code == "LINK_ARRAY_SUPPRESSED_GEOMETRY_UNVERIFIED")'
)
_ARRAY_PATH = (
    '$.native.link_arrays[*] ? ('
    '@.status == "unavailable" || @.truncated == true || '
    '!exists(@.suppression_summary) || '
    '@.suppression_summary.type() != "object" || '
    '!exists(@.suppression_summary.unknown_inspected_count) || '
    '!exists(@.suppression_summary.uninspected_count) || '
    '@.suppression_summary.unknown_inspected_count.type() != "number" || '
    '@.suppression_summary.uninspected_count.type() != "number" || '
    '@.suppression_summary.unknown_inspected_count < 0 || '
    '@.suppression_summary.uninspected_count < 0 || '
    '@.suppression_summary.unknown_inspected_count > 0 || '
    '@.suppression_summary.uninspected_count > 0)'
)

ARRAY_GEOMETRY_WARNING = (
    "Array geometry or element activity is unverified; combined measurements, mass and array "
    "quantities may include suppressed or uninspected elements."
)


def has_unverified_array_geometry(metadata: dict[str, Any]) -> bool:
    """Recognize current diagnostics and array evidence from older extractions."""
    native = metadata.get("native")
    if not isinstance(native, dict):
        return False
    diagnostics = native.get("extraction_diagnostics")
    if isinstance(diagnostics, list) and any(
        isinstance(issue, dict) and issue.get("code") in {
            "LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN", "LINK_ARRAY_EVIDENCE_TRUNCATED",
            "LINK_ARRAY_EXTRACTION_FAILED", "LINK_ARRAY_SUPPRESSED_GEOMETRY_UNVERIFIED",
        }
        for issue in diagnostics
    ):
        return True
    arrays = native.get("link_arrays")
    if not isinstance(arrays, list):
        return False
    for array in arrays:
        if (
            not isinstance(array, dict)
            or array.get("status") == "unavailable"
            or array.get("truncated")
        ):
            return True
        summary = array.get("suppression_summary")
        if not isinstance(summary, dict) or any(
            isinstance(summary.get(name), bool)
            or not isinstance(summary.get(name), (int, float))
            or summary[name] != 0
            for name in ("unknown_inspected_count", "uninspected_count")
        ):
            return True
    return False


def array_geometry_unverified(metadata_column):
    """Return a PostgreSQL predicate consistent with the AI evidence guard."""
    return or_(
        func.coalesce(
            func.jsonb_path_exists(metadata_column, cast(literal(_DIAGNOSTIC_PATH), JSONPATH)),
            False,
        ),
        func.coalesce(
            func.jsonb_path_exists(metadata_column, cast(literal(_ARRAY_PATH), JSONPATH)), False
        ),
    )
