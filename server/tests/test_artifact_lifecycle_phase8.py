import hashlib
import uuid

from artifact_helpers import commit_item
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.db import SessionLocal
from modelryx.models import (
    Artifact,
    ArtifactFile,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
)
from sqlalchemy import select

client = TestClient(app)


def _released_item() -> dict:
    committed = commit_item(
        client,
        item_number=f"PF-RELEASE-{uuid.uuid4().hex}",
        name="Released bracket",
        files=[("bracket.step", b"released-authoritative-content")],
    )
    with SessionLocal() as db:
        revision = db.get(Revision, uuid.UUID(committed["revision_id"]))
        revision.state = "READY"
        db.commit()
    response = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/{committed['revision_id']}/release",
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    return {**committed, "release": response.json()}


def test_release_freezes_revision_checkout_and_edit_staging() -> None:
    result = _released_item()
    assert result["release"]["lifecycle_state"] == "RELEASED"
    assert result["release"]["released_at"] is not None

    checkout = client.post(
        f"/api/v1/items/{result['item_id']}/revisions/{result['revision_id']}/checkout",
        headers=AUTH,
    )
    assert checkout.status_code == 409
    assert "create a new Item Revision" in checkout.json()["detail"]

    edit = client.post(
        f"/api/v1/staging/items/{result['item_id']}/revisions/{result['revision_id']}/edit",
        headers=AUTH,
        json={"client_staging_id": str(uuid.uuid4())},
    )
    assert edit.status_code == 409
    assert "create a new Item Revision" in edit.json()["detail"]


def test_copying_released_revision_creates_a_working_revision() -> None:
    result = _released_item()
    copied = client.post(
        f"/api/v1/items/{result['item_id']}/revisions/copy",
        headers=AUTH,
        json={"source_revision_id": result["revision_id"], "revision_code": "B"},
    )
    assert copied.status_code == 201, copied.text
    assert copied.json()["lifecycle_state"] == "WORKING"
    source = client.get(
        f"/api/v1/items/{result['item_id']}/revisions/{result['revision_id']}", headers=AUTH
    )
    assert source.json()["lifecycle_state"] == "RELEASED"


def test_revise_copies_reuses_and_regenerates_by_relation_type() -> None:
    committed = commit_item(
        client,
        item_number=f"PF-REVISE-{uuid.uuid4().hex}",
        name="Revise policy",
        files=[("policy.step", b"authoritative-policy-content")],
    )
    with SessionLocal() as db:
        revision = db.get(Revision, uuid.UUID(committed["revision_id"]))
        revision.state = "READY"
        snapshot = db.scalar(
            select(RevisionContentSnapshot).where(
                RevisionContentSnapshot.revision_id == revision.id
            )
        )
        source_relation = db.scalar(
            select(RevisionArtifactRelation).where(
                RevisionArtifactRelation.revision_id == revision.id,
                RevisionArtifactRelation.relation_type == "SPECIFICATION",
            )
        )
        reference = Artifact(
            artifact_type="DOCUMENT",
            name="Shared standard",
            classification="EXTERNAL_REFERENCE",
            source="USER",
            state="READY",
            created_by=revision.created_by,
        )
        representation = Artifact(
            artifact_type="GLB",
            name="Generated mesh",
            classification="DERIVED",
            source="WORKER",
            derived_from_artifact_id=source_relation.artifact_id,
            state="READY",
            created_by=revision.created_by,
        )
        analysis = Artifact(
            artifact_type="METADATA",
            name="Generated metadata",
            classification="DERIVED",
            source="WORKER",
            derived_from_artifact_id=source_relation.artifact_id,
            state="READY",
            created_by=revision.created_by,
        )
        db.add_all([reference, representation, analysis])
        db.flush()
        policy_relations = [
            (reference, "REFERENCE"),
            (representation, "REPRESENTATION"),
            (analysis, "ANALYSIS"),
        ]
        for sequence, (artifact, relation_type) in enumerate(
            policy_relations,
            start=1,
        ):
            relation = RevisionArtifactRelation(
                revision_id=revision.id,
                artifact_id=artifact.id,
                relation_type=relation_type,
                sequence=sequence,
                created_by=revision.created_by,
            )
            db.add(relation)
            db.flush()
            db.add(
                RevisionContentSnapshotArtifact(
                    snapshot_id=snapshot.id,
                    relation_id=relation.id,
                    sequence=sequence,
                )
            )
        db.commit()
        reference_id = str(reference.id)
        source_artifact_id = str(source_relation.artifact_id)

    released = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/{committed['revision_id']}/release",
        headers=AUTH,
    )
    assert released.status_code == 200, released.text
    copied = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/copy",
        headers=AUTH,
        json={"source_revision_id": committed["revision_id"], "revision_code": "B"},
    )
    assert copied.status_code == 201, copied.text
    relations = client.get(
        f"/api/v1/item-revisions/{copied.json()['id']}/artifacts", headers=AUTH
    ).json()
    by_type = {relation["relation_type"]: relation for relation in relations}

    assert by_type["SPECIFICATION"]["artifact"]["id"] != source_artifact_id
    assert by_type["REFERENCE"]["artifact"]["id"] == reference_id
    assert "REPRESENTATION" not in by_type
    assert "ANALYSIS" not in by_type


def test_upload_preflight_returns_current_artifact_revision_identity() -> None:
    result = _released_item()
    item = client.get(f"/api/v1/items/{result['item_id']}", headers=AUTH).json()
    content = b"released-authoritative-content"

    response = client.post(
        "/api/v1/staging/upload-preflight",
        headers=AUTH,
        json={
            "files": [
                {
                    "logical_path": "bracket.step",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "byte_size": len(content),
                    "item_number": item["item_number"],
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    review = response.json()["files"][0]
    assert review["item_number_exists"] is True
    assert review["existing_item_id"] == result["item_id"]
    assert result["revision_id"] in {
        match["revision_id"] for match in review["duplicates"]
    }
    assert all("working_version_id" not in match for match in review["duplicates"])


def test_upload_preflight_does_not_treat_assembly_dependency_as_parent_identity() -> None:
    result = _released_item()
    dependency = b"assembly-package-secondary-file"
    with SessionLocal() as db:
        relation = db.scalar(
            select(RevisionArtifactRelation).where(
                RevisionArtifactRelation.revision_id == uuid.UUID(result["revision_id"]),
                RevisionArtifactRelation.relation_type == "SPECIFICATION",
            )
        )
        db.add(
            ArtifactFile(
                artifact_id=relation.artifact_id,
                reference_name="components/dependency.ipt",
                role="SECONDARY",
                revision_id=uuid.UUID(result["revision_id"]),
                kind="NATIVE",
                storage_key=f"test/preflight/{uuid.uuid4()}/dependency.ipt",
                original_filename="dependency.ipt",
                mime_type="application/octet-stream",
                byte_size=len(dependency),
                sha256=hashlib.sha256(dependency).hexdigest(),
                state="VERIFIED",
                required=False,
            )
        )
        db.commit()

    response = client.post(
        "/api/v1/staging/upload-preflight",
        headers=AUTH,
        json={
            "files": [
                {
                    "logical_path": "components/dependency.ipt",
                    "sha256": hashlib.sha256(dependency).hexdigest(),
                    "byte_size": len(dependency),
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["files"][0]["duplicates"] == []


def _local_part_metadata() -> dict:
    return {
        "schema": "modelryx.part-metadata",
        "schema_version": "1.0",
        "source": {
            "system": "freecad",
            "system_version": "1.0",
            "worker": "modelryx-local-cad-agent",
            "worker_version": "test",
            "extracted_at": "2026-10-25T12:00:00Z",
        },
        "identity": {"name": "Local bracket", "kind": "part"},
        "geometry": {
            "bounding_box": {"x_mm": 12, "y_mm": 8, "z_mm": 2},
            "shape": {"volume_mm3": 80, "surface_area_mm2": 120},
            "topology": {"solid_count": 1, "face_count": 6},
        },
        "assembly": {"is_assembly": False},
        "quality": {
            "geometry": "complete",
            "physical": "unavailable",
            "features": "unsupported",
            "assembly": "not_applicable",
            "warnings": [],
        },
    }


def test_local_staging_metadata_is_bound_to_declared_root_hash() -> None:
    content = b"locally-analyzed-cad"
    source_hash = hashlib.sha256(content).hexdigest()
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "files": [{
                "logical_path": "bracket.FCStd",
                "filename": "bracket.FCStd",
                "byte_size": len(content),
                "sha256": source_hash,
                "mime_type": "application/octet-stream",
            }],
        },
    )
    assert created.status_code == 201, created.text
    staging_id = created.json()["id"]

    mismatch = client.post(
        f"/api/v1/staging/{staging_id}/metadata",
        headers=AUTH,
        json={
            "metadata": _local_part_metadata(),
            "source_logical_path": "bracket.FCStd",
            "source_sha256": "0" * 64,
            "analyzer": "modelryx-local-cad-agent-test",
        },
    )
    assert mismatch.status_code == 409
    assert "does not match" in mismatch.json()["detail"]

    accepted = client.post(
        f"/api/v1/staging/{staging_id}/metadata",
        headers=AUTH,
        json={
            "metadata": _local_part_metadata(),
            "source_logical_path": "bracket.FCStd",
            "source_sha256": source_hash,
            "analyzer": "modelryx-local-cad-agent-test",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["result"]["local_metadata"] == {
        "source_logical_path": "bracket.FCStd",
        "source_sha256": source_hash,
        "analyzer": "modelryx-local-cad-agent-test",
    }
    assert accepted.json()["duplicate_check"]["algorithm_version"] == "part-similarity-v1"
