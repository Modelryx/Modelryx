from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from modelryx.metadata import MetadataSchemaError, MetadataService
from test_metadata_schema import valid_metadata


def test_complete_metadata_is_ready() -> None:
    metadata = valid_metadata()
    metadata["quality"].update(
        {"physical": "complete", "features": "complete", "assembly": "not_applicable"}
    )
    assert MetadataService.status_for(metadata) == "READY"


def test_missing_optional_metadata_is_partial() -> None:
    assert MetadataService.status_for(valid_metadata()) == "PARTIAL"


def test_incomplete_geometry_is_invalid() -> None:
    metadata = deepcopy(valid_metadata())
    metadata["quality"]["geometry"] = "partial"
    assert MetadataService.status_for(metadata) == "INVALID"


def test_drawing_metadata_does_not_require_solid_geometry() -> None:
    metadata = deepcopy(valid_metadata())
    metadata["quality"].update(
        {"geometry": "not_applicable", "physical": "not_applicable", "features": "not_applicable"}
    )
    metadata["drawings"] = [{"page_count": 2}]
    assert MetadataService.status_for(metadata) == "READY"
    metadata["drawings"] = []
    assert MetadataService.status_for(metadata) == "INVALID"


def test_assembly_occurrence_evidence_survives_unavailable_aggregate_geometry() -> None:
    metadata = deepcopy(valid_metadata())
    metadata["identity"]["kind"] = "assembly"
    metadata["assembly"].update(
        {"is_assembly": True, "components": [{"occurrence_id": "placement"}]}
    )
    metadata["quality"]["geometry"] = "unavailable"
    assert MetadataService.status_for(metadata) == "PARTIAL"
    metadata["assembly"]["components"] = []
    assert MetadataService.status_for(metadata) == "INVALID"


def test_assembly_quality_participates_for_assemblies() -> None:
    metadata = valid_metadata()
    metadata["identity"]["kind"] = "assembly"
    metadata["quality"].update(
        {"physical": "complete", "features": "complete", "assembly": "partial"}
    )
    assert MetadataService.status_for(metadata) == "PARTIAL"


def test_service_validates_and_appends_immutable_extraction() -> None:
    db = Mock()
    revision = SimpleNamespace(
        id=uuid4(), metadata_status="UNAVAILABLE", current_metadata_extraction_id=None
    )
    snapshot = SimpleNamespace(id=uuid4())
    raw_artifact = SimpleNamespace(id=uuid4())
    metadata = valid_metadata()

    extraction = MetadataService(db).ingest(
        revision=revision,
        snapshot=snapshot,
        raw_artifact=raw_artifact,
        metadata=metadata,
    )

    db.add.assert_called_once_with(extraction)
    assert extraction.revision_id == revision.id
    assert extraction.raw_artifact_id == raw_artifact.id
    assert extraction.metadata_json is metadata
    assert extraction.status == "PARTIAL"
    assert extraction.derived_json["bbox_sorted_mm"] == [12, 80, 120]
    assert extraction.bbox_max_mm == 120
    assert extraction.volume_mm3 == 45000
    assert extraction.solid_count == 1
    assert revision.metadata_status == "PARTIAL"
    assert revision.current_metadata_extraction_id == extraction.id


def test_service_updates_embedded_part_metadata_with_enriched_result() -> None:
    db = Mock()
    metadata = valid_metadata()
    metadata["native"] = {"parameters": [{"name": "Length", "value": 120.0}]}
    revision = SimpleNamespace(
        id=uuid4(),
        part_id=None,
        metadata_status="UNAVAILABLE",
        current_metadata_extraction_id=None,
        derived_metadata_json={"partMetadata": metadata},
    )

    extraction = MetadataService(db).ingest(
        revision=revision,
        snapshot=SimpleNamespace(id=uuid4()),
        raw_artifact=SimpleNamespace(id=uuid4()),
        metadata=metadata,
    )

    assert revision.derived_metadata_json["partMetadata"] is extraction.metadata_json
    assert revision.derived_metadata_json["partMetadata"]["parameters"][0]["name"] == "Length"
    assert "extraction" in revision.derived_metadata_json["partMetadata"]


def test_service_does_not_persist_schema_invalid_metadata() -> None:
    db = Mock()
    metadata = valid_metadata()
    metadata["geometry"]["bounding_box"]["x_mm"] = "invalid"
    with pytest.raises(MetadataSchemaError):
        MetadataService(db).ingest(
            revision=SimpleNamespace(
                id=uuid4(), metadata_status="UNAVAILABLE", current_metadata_extraction_id=None
            ),
            snapshot=SimpleNamespace(id=uuid4()),
            raw_artifact=SimpleNamespace(id=uuid4()),
            metadata=metadata,
        )
    db.add.assert_not_called()
