import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from modelryx.api.metadata import extraction_response, router


def test_extraction_response_exposes_provenance_and_raw_link() -> None:
    extraction = SimpleNamespace(
        id=uuid.uuid4(),
        revision_id=uuid.uuid4(),
        snapshot_id=uuid.uuid4(),
        raw_artifact_id=uuid.uuid4(),
        schema_version="1.0",
        worker_type="modelryx-freecad-worker",
        worker_version="modelryx-fcstd-5",
        source_system="freecad",
        source_system_version="1.0.2",
        status="PARTIAL",
        extracted_at=datetime.now(UTC),
        metadata_json={"schema": "modelryx.part-metadata"},
        derived_json={"bbox_sorted_mm": [10, 20, 30]},
        warnings_json=["Material unavailable"],
        errors_json=[],
        created_at=datetime.now(UTC),
    )
    response = extraction_response(extraction)
    assert response.worker_version == "modelryx-fcstd-5"
    assert response.snapshot_id == extraction.snapshot_id
    assert response.derived["bbox_sorted_mm"] == [10, 20, 30]
    assert response.raw_metadata_url == (
        f"/api/v1/revisions/{extraction.revision_id}/metadata/extractions/{extraction.id}/raw"
    )


def test_metadata_inspection_routes_are_registered() -> None:
    paths = {route.path for route in router.routes}
    assert "/api/v1/revisions/{revision_id}/metadata" in paths
    assert "/api/v1/revisions/{revision_id}/metadata/extractions" in paths
    assert "/api/v1/revisions/{revision_id}/metadata/extractions/{extraction_id}/raw" in paths
