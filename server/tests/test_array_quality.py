from modelryx.metadata.array_quality import (
    array_geometry_unverified,
    has_unverified_array_geometry,
)
from sqlalchemy import column, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB


def test_unknown_array_activity_is_conservative_for_missing_and_invalid_counts() -> None:
    for array in (
        {"status": "unavailable"},
        {"suppression_summary": {}},
        {"suppression_summary": {"unknown_inspected_count": "0", "uninspected_count": 0}},
        {"suppression_summary": {"unknown_inspected_count": 0, "uninspected_count": 1}},
    ):
        assert has_unverified_array_geometry({"native": {"link_arrays": [array]}})
    verified = {"suppression_summary": {"unknown_inspected_count": 0, "uninspected_count": 0}}
    assert not has_unverified_array_geometry({"native": {"link_arrays": [verified]}})


def test_sql_quality_guard_uses_parameterized_postgresql_jsonpaths() -> None:
    statement = select(array_geometry_unverified(column("metadata_json", JSONB)))
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "jsonb_path_exists" in str(compiled)
    assert any(
        "LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN" in str(value) for value in compiled.params.values()
    )
    assert any("unknown_inspected_count" in str(value) for value in compiled.params.values())
