import io
import json
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from artifact_helpers import commit_item
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import ProcessingJob, Revision
from modelryx.services import processing
from sqlalchemy import select

client = TestClient(app)


def test_licensed_step_output_keeps_converter_compatible_extension() -> None:
    assert processing.external_output_filename("step", "example_cad", 2) == "model.step"
    assert processing.external_output_filename("part-metadata", "example_cad", 1) == (
        "part-metadata.json"
    )
    assert processing.external_output_filename("xref-dependencies", "example_cad", 3) == (
        "xref-dependencies.json"
    )


def test_example_cad_assembly_upload_preserves_dependency_paths(tmp_path: Path) -> None:
    stored = tmp_path / "stored"
    stored.mkdir()
    (stored / "root").write_bytes(b"assembly")
    (stored / "member").write_bytes(b"part")
    root = SimpleNamespace(
        id=uuid.uuid4(), storage_key="root", original_filename="Frame.assembly",
        reference_name="Frame/Frame.assembly", sha256="root-hash",
    )
    member = SimpleNamespace(
        id=uuid.uuid4(), storage_key="member", original_filename="Member.part",
        reference_name="Frame/Members/Member.part", sha256="member-hash",
    )
    storage = SimpleNamespace(path_for=lambda key: stored / key)

    package, root_path = processing.prepare_external_source_upload(
        storage, root, [root, member], tmp_path, "example_cad"
    )

    assert root_path == "Frame/Frame.assembly"
    with zipfile.ZipFile(package) as archive:
        assert archive.read("Frame/Frame.assembly") == b"assembly"
        assert archive.read("Frame/Members/Member.part") == b"part"


def test_worker_model_preview_contract_rejects_broken_navigation() -> None:
    valid = {
        "schemaVersion": 1,
        "rootOccurrenceId": "example_cad-root",
        "items": [
            {
                "occurrenceId": "example_cad-root",
                "partId": "example_cad-root",
                "name": "Assembly.sheet",
                "parentOccurrenceId": None,
            },
            {
                "occurrenceId": "model/10",
                "partId": "definition-A",
                "name": "Bracket",
                "parentOccurrenceId": "example_cad-root",
                "renderOccurrenceIds": ["model/10/2A", "model/10/2B"],
            },
        ],
    }

    assert processing.validate_model_preview(valid) is valid
    valid["items"][1]["parentOccurrenceId"] = "missing"
    with pytest.raises(RuntimeError, match="unknown parent occurrence"):
        processing.validate_model_preview(valid)


def _minimal_glb() -> bytes:
    return b"glTF" + (2).to_bytes(4, "little") + (12).to_bytes(4, "little")


def test_step_upload_is_primary_and_publishes_glb_preview(monkeypatch) -> None:
    suffix = uuid.uuid4().hex
    source = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"
    committed = commit_item(
        client,
        item_number=f"STEP-{suffix}",
        name="Neutral STEP part",
        files=[("bracket.step", source)],
        cad_system="STEP",
        native_format="STEP",
    )

    def convert(_source: Path, output: Path, _settings, *, cancellation_callback, job_id) -> dict:
        assert job_id is not None
        cancellation_callback()
        (output / "model.glb").write_bytes(_minimal_glb())
        metadata = {
            "schema": "modelryx.part-metadata",
            "schema_version": "1.0",
            "source": {
                "system": "step",
                "worker": "modelryx-step-worker",
                "worker_version": "2.0.0",
                "extracted_at": "2026-09-29T12:00:00Z",
            },
            "identity": {"name": "bracket", "kind": "part"},
            "geometry": {
                "bounding_box": {"x_mm": 10, "y_mm": 20, "z_mm": 30},
                "shape": {"volume_mm3": 1000, "surface_area_mm2": 700},
                "topology": {"solid_count": 1, "face_count": 6, "edge_count": 12},
            },
            "assembly": {"is_assembly": False},
            "properties": {"source_format": "STEP"},
            "quality": {
                "geometry": "complete",
                "physical": "unavailable",
                "features": "unsupported",
                "assembly": "not_applicable",
                "warnings": [],
            },
        }
        (output / "part-metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        (output / "step-raw-metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        (output / "model-preview.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "rootOccurrenceId": "step-root",
                        "items": [
                            {
                                "name": "bracket",
                                "nodeType": "part",
                                "partId": "step-model",
                                "occurrenceId": "step-model",
                                "parentOccurrenceId": "step-root",
                                "source": {
                                    "system": "STEP",
                                    "filename": "bracket.step",
                                },
                            }
                        ],
                }
            ),
            encoding="utf-8",
        )
        return {"worker_version": "2.0.0"}

    monkeypatch.setattr(processing, "run_step_processing_job", convert)
    with SessionLocal() as db:
        job = db.scalar(
            select(ProcessingJob).where(
                ProcessingJob.revision_id == uuid.UUID(committed["revision_id"]),
                ProcessingJob.type == "STEP_TO_MODELRYX",
            )
        )
        assert job is not None
        job.state = "RUNNING"
        job.attempt_count = 1
        db.commit()
        processing.process_job(db, job, get_settings())
        revision = db.get(Revision, uuid.UUID(committed["revision_id"]))
        assert revision.state == "READY_WITH_WARNINGS"
        assert revision.cad_system == "STEP"
        assert revision.native_format == "STEP"
        assert revision.metadata_status == "PARTIAL"
        assert revision.current_metadata_extraction_id is not None

    relations = client.get(
        f"/api/v1/item-revisions/{committed['revision_id']}/artifacts", headers=AUTH
    )
    assert relations.status_code == 200, relations.text
    authoritative = next(
        row for row in relations.json() if row["artifact"]["classification"] == "AUTHORITATIVE"
    )
    assert authoritative["artifact"]["artifact_type"] == "STEP"
    assert authoritative["is_primary"] is True

    preview = client.get(
        f"/api/v1/revisions/{committed['revision_id']}/preview", headers=AUTH
    )
    assert preview.status_code == 200, preview.text
    with zipfile.ZipFile(io.BytesIO(preview.content)) as package:
        assert package.read("model.glb") == _minimal_glb()
        model = json.loads(package.read("model-preview.json"))
    assert model["rootOccurrenceId"] == "step-root"
    assert model["items"][0]["source"]["filename"] == "bracket.step"

    properties = client.get(
        f"/api/v1/revisions/{committed['revision_id']}/metadata", headers=AUTH
    )
    assert properties.status_code == 200, properties.text
    assert properties.json()["current"]["metadata"]["geometry"]["bounding_box"] == {
        "x_mm": 10,
        "y_mm": 20,
        "z_mm": 30,
    }

    library = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={"filters": {"cad_system": "STEP"}, "limit": 50},
    )
    assert library.status_code == 200, library.text
    assert any(
        result["item_number"] == f"STEP-{suffix}"
        and result["cad_system"] == "STEP"
        for result in library.json()["results"]
    )

    regenerated = client.post(
        f"/api/v1/revisions/{committed['revision_id']}/preview/reprocess", headers=AUTH
    )
    assert regenerated.status_code == 202, regenerated.text
    assert regenerated.json()["type"] == "STEP_TO_MODELRYX"

    copied = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/copy",
        headers=AUTH,
        json={
            "source_revision_id": committed["revision_id"],
            "revision_code": "B",
        },
    )
    assert copied.status_code == 201, copied.text
    assert copied.json()["state"] == "PROCESSING"
    with SessionLocal() as db:
        copied_job = db.scalar(
            select(ProcessingJob).where(
                ProcessingJob.revision_id == uuid.UUID(copied.json()["id"]),
                ProcessingJob.type == "STEP_TO_MODELRYX",
            )
        )
        assert copied_job is not None
        assert copied_job.idempotency_key.startswith("step-preview:")
