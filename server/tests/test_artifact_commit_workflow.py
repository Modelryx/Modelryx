import hashlib
import uuid

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    Artifact,
    ArtifactFile,
    BomLine,
    BomViewRevision,
    Item,
    ProcessingJob,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    StagingSession,
    User,
    WorkingVersion,
)
from modelryx.services.storage import LocalStorageProvider
from sqlalchemy import func, select

client = TestClient(app)


def local_freecad_metadata(*, kind: str = "assembly") -> dict:
    return {
        "schema": "modelryx.part-metadata",
        "schema_version": "1.0",
        "source": {
            "system": "freecad",
            "system_version": "1.1",
            "worker": "modelryx-freecad-worker",
            "worker_version": "commit-test",
            "extracted_at": "2026-10-25T12:00:00Z",
        },
        "identity": {"name": "Local preflight", "kind": kind},
        "geometry": {
            "bounding_box": {"x_mm": 10, "y_mm": 10, "z_mm": 10},
            "shape": {"volume_mm3": 1000, "surface_area_mm2": 600},
            "topology": {"solid_count": 1},
        },
        "physical": {},
        "assembly": {"is_assembly": kind == "assembly"},
        "quality": {
            "geometry": "complete",
            "physical": "unavailable",
            "features": "unavailable",
            "assembly": "complete" if kind == "assembly" else "not_applicable",
            "warnings": [],
        },
    }


def item_exists(item_number: str) -> bool:
    with SessionLocal() as db:
        return db.scalar(select(Item.id).where(Item.part_number == item_number)) is not None


def test_external_part_commit_attaches_related_drawings_without_creating_items(
    example_worker,
) -> None:
    item_number = f"EXAMPLE_CAD-DRAWING-{uuid.uuid4().hex}"
    files = {
        "Oil Pan.part": b"example_cad-part",
        "Oil Pan.drawing": b"example_cad-idw",
        "Oil Pan.sheet": b"example_cad-dwg",
    }
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "files": [
                {
                    "logical_path": path,
                    "filename": path,
                    "mime_type": "application/octet-stream",
                    "byte_size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for path, content in files.items()
            ],
        },
    )
    assert created.status_code == 201, created.text
    staging = created.json()
    staged = {row["logical_path"]: row for row in staging["files"]}
    for path, content in files.items():
        uploaded = client.put(
            staged[path]["upload_url"],
            headers={**AUTH, "Content-Type": "application/octet-stream"},
            content=content,
        )
        assert uploaded.status_code == 200, uploaded.text
        staging = uploaded.json()

    proposed = client.put(
        f"/api/v1/staging/{staging['id']}/proposal",
        headers=AUTH,
        json={
            "expected_snapshot_version": staging["snapshot_version"],
            "operation": "CREATE_ITEM",
            "item": {"item_number": item_number, "name": "Oil Pan", "item_type": "PART"},
            "revision": {
                "revision_code": "A",
                "cad_system": "Example CAD",
                "native_format": "PART",
            },
            "artifacts": [
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "artifact_type": "CAD_MODEL",
                    "name": "Oil Pan",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": staged["Oil Pan.part"]["id"],
                            "reference_name": "Oil Pan.part",
                            "role": "PRIMARY",
                        }
                    ],
                },
                *[
                    {
                        "client_artifact_id": str(uuid.uuid4()),
                        "artifact_type": "DRAWING",
                        "name": path.rsplit(".", 1)[0],
                        "classification": "AUTHORITATIVE",
                        "relation_type": "DRAWING",
                        "is_primary": False,
                        "files": [
                            {
                                "staged_file_id": staged[path]["id"],
                                "reference_name": path,
                                "role": "PRIMARY",
                            }
                        ],
                    }
                    for path in ("Oil Pan.drawing", "Oil Pan.sheet")
                ],
            ],
            "identity_decisions": [
                {"staged_file_id": staged["Oil Pan.part"]["id"], "decision": "CREATE_NEW"}
            ],
            "bom": [],
        },
    )
    assert proposed.status_code == 200, proposed.text
    staging = proposed.json()
    validated = client.post(f"/api/v1/staging/{staging['id']}/validate", headers=AUTH)
    assert validated.status_code == 200, validated.text
    ready = client.post(
        f"/api/v1/staging/{staging['id']}/ready",
        headers=AUTH,
        json={"expected_snapshot_version": staging["snapshot_version"]},
    )
    assert ready.status_code == 200, ready.text
    committed = client.post(
        f"/api/v1/uploads/{staging['id']}/commit",
        headers=AUTH,
        json={
            "snapshot_version": ready.json()["snapshot_version"],
            "commit_id": str(uuid.uuid4()),
        },
    )
    assert committed.status_code == 200, committed.text
    committed_result = committed.json()
    listed = client.get("/api/v1/staging?state=all&limit=100", headers=AUTH)
    assert listed.status_code == 200, listed.text
    stage_summary = next(row for row in listed.json()["items"] if row["id"] == staging["id"])
    assert set(stage_summary["created_revision_ids"]) == set(
        committed_result["created_revision_ids"]
    )

    with SessionLocal() as db:
        item = db.scalar(select(Item).where(Item.part_number == item_number))
        assert item is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(Item)
                .where(Item.part_number.like(f"{item_number}%"))
            )
            == 1
        )
        artifact_types = list(
            db.scalars(
                select(Artifact.artifact_type)
                .join(RevisionArtifactRelation, RevisionArtifactRelation.artifact_id == Artifact.id)
                .where(RevisionArtifactRelation.revision_id == committed.json()["revision_id"])
            )
        )
        assert artifact_types.count("CAD_MODEL") == 1
        assert artifact_types.count("DRAWING") == 2


def test_create_revision_uses_atomic_artifact_staging():
    item_number = f"REV-{uuid.uuid4().hex}"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        assert user is not None
        item = Item(
            part_number=item_number, name="Revision target", part_type="PART", created_by=user.id
        )
        db.add(item)
        db.flush()
        base = Revision(
            part_id=item.id,
            revision_code="A",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={},
            state="READY",
            created_by=user.id,
        )
        db.add(base)
        db.flush()
        base_version = WorkingVersion(
            revision_id=base.id,
            version_number=1,
            state="READY",
            metadata_json={},
            created_by=user.id,
        )
        db.add(base_version)
        db.flush()
        base.current_working_version_id = base_version.id
        item.current_revision_id = base.id
        db.commit()
        item_id, base_id = str(item.id), str(base.id)

    content = b"new-revision-artifact"
    digest = hashlib.sha256(content).hexdigest()
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "operation": "CREATE_REVISION",
            "item_id": item_id,
            "base_revision_id": base_id,
            "files": [
                {
                    "logical_path": "RevisionB.FCStd",
                    "filename": "RevisionB.FCStd",
                    "byte_size": len(content),
                    "sha256": digest,
                    "mime_type": "application/octet-stream",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    staging = created.json()
    staged_file = staging["files"][0]
    uploaded = client.put(
        staged_file["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text
    proposal = client.put(
        f"/api/v1/staging/{staging['id']}/proposal",
        headers=AUTH,
        json={
            "expected_snapshot_version": staging["snapshot_version"],
            "operation": "CREATE_REVISION",
            "destination": {"item_id": item_id, "revision_id": base_id},
            "item": {"item_number": item_number, "name": "Revision target", "item_type": "PART"},
            "revision": {"revision_code": "B", "cad_system": "FreeCAD", "native_format": "FCStd"},
            "artifacts": [
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "artifact_type": "CAD_MODEL",
                    "name": "Revision B",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": staged_file["id"],
                            "reference_name": "MODEL",
                            "role": "PRIMARY",
                        }
                    ],
                }
            ],
            "identity_decisions": [{"staged_file_id": staged_file["id"], "decision": "CREATE_NEW"}],
            "bom": [],
        },
    )
    assert proposal.status_code == 200, proposal.text
    version = proposal.json()["snapshot_version"]
    validation = client.post(f"/api/v1/staging/{staging['id']}/validate", headers=AUTH)
    assert validation.status_code == 200, validation.text
    ready = client.post(
        f"/api/v1/staging/{staging['id']}/ready",
        headers=AUTH,
        json={"expected_snapshot_version": version},
    )
    assert ready.status_code == 200, ready.text
    committed = client.post(
        f"/api/v1/uploads/{staging['id']}/commit",
        headers=AUTH,
        json={
            "snapshot_version": version,
            "commit_id": str(uuid.uuid4()),
        },
    )
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert result["item_id"] == item_id
    assert result["revision_id"] != base_id
    with SessionLocal() as db:
        item = db.get(Item, uuid.UUID(item_id))
        assert item is not None and str(item.current_revision_id) == result["revision_id"]


def test_new_item_stays_in_staging_until_atomic_artifact_commit():
    content = f"artifact-commit-{uuid.uuid4()}".encode()
    digest = hashlib.sha256(content).hexdigest()
    item_number = f"ART-{uuid.uuid4().hex}"
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "operation": "CREATE_ITEM",
            "files": [
                {
                    "logical_path": "Bracket.FCStd",
                    "filename": "Bracket.FCStd",
                    "byte_size": len(content),
                    "sha256": digest,
                    "mime_type": "application/octet-stream",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    staging = created.json()
    staged_file = staging["files"][0]
    assert not item_exists(item_number)

    uploaded = client.put(
        staged_file["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text
    artifact_key = uuid.uuid4()
    proposal = client.put(
        f"/api/v1/staging/{staging['id']}/proposal",
        headers=AUTH,
        json={
            "expected_snapshot_version": staging["snapshot_version"],
            "operation": "CREATE_ITEM",
            "item": {"item_number": item_number, "name": "Bracket", "item_type": "PART"},
            "revision": {
                "revision_code": "A",
                "cad_system": "FreeCAD",
                "native_format": "FCStd",
            },
            "artifacts": [
                {
                    "client_artifact_id": str(artifact_key),
                    "artifact_type": "CAD_MODEL",
                    "name": "Bracket",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": staged_file["id"],
                            "reference_name": "MODEL",
                            "role": "PRIMARY",
                        }
                    ],
                }
            ],
            "identity_decisions": [{"staged_file_id": staged_file["id"], "decision": "CREATE_NEW"}],
            "bom": [],
        },
    )
    assert proposal.status_code == 200, proposal.text
    snapshot_version = proposal.json()["snapshot_version"]
    validation = client.post(f"/api/v1/staging/{staging['id']}/validate", headers=AUTH)
    assert validation.status_code == 200, validation.text
    assert validation.json()["snapshot_version"] == snapshot_version
    assert validation.json()["status"] == "READY_WITH_WARNINGS"
    assert not item_exists(item_number)

    ready = client.post(
        f"/api/v1/staging/{staging['id']}/ready",
        headers=AUTH,
        json={"expected_snapshot_version": snapshot_version},
    )
    assert ready.status_code == 200, ready.text
    committed = client.post(
        f"/api/v1/uploads/{staging['id']}/commit",
        headers=AUTH,
        json={"snapshot_version": snapshot_version, "commit_id": str(uuid.uuid4())},
    )
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert item_exists(item_number)
    artifacts = client.get(
        f"/api/v1/item-revisions/{result['revision_id']}/artifacts", headers=AUTH
    )
    assert artifacts.status_code == 200, artifacts.text
    assert artifacts.json()[0]["artifact"]["artifact_key"] == str(artifact_key)
    files = client.get(f"/api/v1/artifacts/{result['artifact_ids'][0]}/files", headers=AUTH)
    assert files.status_code == 200, files.text
    assert files.json()[0]["reference_name"] == "MODEL"
    assert files.json()[0]["download_url"].startswith(
        f"/api/v1/artifacts/{result['artifact_ids'][0]}/files/"
    )

    edit = client.post(
        f"/api/v1/staging/items/{result['item_id']}/revisions/{result['revision_id']}/edit",
        headers=AUTH,
        json={"client_staging_id": str(uuid.uuid4())},
    )
    assert edit.status_code == 201, edit.text
    edit_staging = edit.json()
    assert edit_staging["operation"] == "EDIT_ITEM_REVISION"
    assert edit_staging["base_revision_id"] == result["revision_id"]
    assert edit_staging["base_snapshot_id"] == result["content_snapshot_id"]
    assert edit_staging["files"] == []
    resumed = client.post(
        f"/api/v1/staging/items/{result['item_id']}/revisions/{result['revision_id']}/edit",
        headers=AUTH,
        json={"client_staging_id": str(uuid.uuid4())},
    )
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["id"] == edit_staging["id"]
    retained = edit_staging["proposal"]["artifacts"]
    assert len(retained) == 1
    assert retained[0]["existing_relation_id"]
    assert retained[0]["files"] == []
    retained_content = edit_staging["retained_artifacts"]
    assert len(retained_content) == 1
    assert retained_content[0]["relation_id"] == retained[0]["existing_relation_id"]
    assert retained_content[0]["files"][0]["filename"] == "Bracket.FCStd"
    download = client.get(retained_content[0]["files"][0]["download_url"], headers=AUTH)
    assert download.status_code == 200, download.text
    assert download.content == content
    checkout = client.get(
        f"/api/v1/items/{result['item_id']}/revisions/{result['revision_id']}/checkout",
        headers=AUTH,
    )
    assert checkout.status_code == 200, checkout.text

    replacement = f"replacement-{uuid.uuid4()}".encode()
    replacement_hash = hashlib.sha256(replacement).hexdigest()
    added = client.post(
        f"/api/v1/staging/{edit_staging['id']}/files",
        headers=AUTH,
        json={
            "expected_snapshot_version": edit_staging["snapshot_version"],
            "logical_path": "Bracket-v2.FCStd",
            "filename": "Bracket-v2.FCStd",
            "byte_size": len(replacement),
            "sha256": replacement_hash,
            "mime_type": "application/octet-stream",
        },
    )
    assert added.status_code == 201, added.text
    edit_staging = added.json()
    staged_file = edit_staging["files"][0]
    uploaded = client.put(
        staged_file["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=replacement,
    )
    assert uploaded.status_code == 200, uploaded.text
    edit_staging = uploaded.json()
    updated_proposal = edit_staging["proposal"]
    updated_proposal["item"]["name"] = "Bracket revised"
    updated_proposal["item"]["description"] = "Updated atomically with the Artifact"
    updated_proposal["revision"]["description"] = "Replacement content"
    updated_proposal["artifacts"] = [
        {
            "client_artifact_id": str(uuid.uuid4()),
            "artifact_type": "CAD_MODEL",
            "name": "Bracket v2",
            "classification": "AUTHORITATIVE",
            "relation_type": "SPECIFICATION",
            "is_primary": True,
            "files": [
                {"staged_file_id": staged_file["id"], "reference_name": "MODEL", "role": "PRIMARY"}
            ],
        }
    ]
    updated = client.put(
        f"/api/v1/staging/{edit_staging['id']}/proposal",
        headers=AUTH,
        json={**updated_proposal, "expected_snapshot_version": edit_staging["snapshot_version"]},
    )
    assert updated.status_code == 200, updated.text
    edit_version = updated.json()["snapshot_version"]
    assert (
        client.post(
            f"/api/v1/staging/{edit_staging['id']}/validate",
            headers=AUTH,
        ).status_code
        == 200
    )
    ready = client.post(
        f"/api/v1/staging/{edit_staging['id']}/ready",
        headers=AUTH,
        json={"expected_snapshot_version": edit_version},
    )
    assert ready.status_code == 200, ready.text
    edit_commit = client.post(
        f"/api/v1/uploads/{edit_staging['id']}/commit",
        headers=AUTH,
        json={"snapshot_version": edit_version, "commit_id": str(uuid.uuid4())},
    )
    assert edit_commit.status_code == 200, edit_commit.text
    edit_result = edit_commit.json()
    assert edit_result["content_snapshot_id"] != result["content_snapshot_id"]
    assert len(edit_result["artifact_ids"]) == 1
    assert (
        client.get(
            f"/api/v1/items/{result['item_id']}/revisions/{result['revision_id']}/checkout",
            headers=AUTH,
        ).status_code
        == 404
    )
    with SessionLocal() as db:
        old_relations = set(
            db.scalars(
                select(RevisionContentSnapshotArtifact.relation_id).where(
                    RevisionContentSnapshotArtifact.snapshot_id
                    == uuid.UUID(result["content_snapshot_id"])
                )
            )
        )
        new_relations = set(
            db.scalars(
                select(RevisionContentSnapshotArtifact.relation_id).where(
                    RevisionContentSnapshotArtifact.snapshot_id
                    == uuid.UUID(edit_result["content_snapshot_id"])
                )
            )
        )
        assert len(old_relations) == 1
        assert len(new_relations) == 1
        assert old_relations.isdisjoint(new_relations)
        current = db.get(Revision, uuid.UUID(result["revision_id"]))
        latest = db.scalar(
            select(RevisionContentSnapshot)
            .where(RevisionContentSnapshot.revision_id == current.id)
            .order_by(RevisionContentSnapshot.sequence.desc())
        )
        assert latest.sequence == 2
        assert current.description == "Replacement content"
        assert db.get(Item, uuid.UUID(result["item_id"])).name == "Bracket revised"

    cancelled = client.post(
        f"/api/v1/staging/items/{result['item_id']}/revisions/{result['revision_id']}/edit",
        headers=AUTH,
        json={"client_staging_id": str(uuid.uuid4())},
    )
    assert cancelled.status_code == 201, cancelled.text
    reopened = cancelled.json()
    assert len(reopened["proposal"]["artifacts"]) == 1
    assert reopened["proposal"]["artifacts"][0]["name"] == "Bracket v2"
    assert len(reopened["retained_artifacts"]) == 1
    current_artifacts = client.get(
        f"/api/v1/item-revisions/{result['revision_id']}/artifacts", headers=AUTH
    )
    assert current_artifacts.status_code == 200, current_artifacts.text
    assert len(current_artifacts.json()) == 1
    assert current_artifacts.json()[0]["artifact"]["name"] == "Bracket v2"
    cancelled_id = reopened["id"]
    assert client.delete(f"/api/v1/staging/{cancelled_id}", headers=AUTH).status_code == 204
    assert (
        client.get(
            f"/api/v1/items/{result['item_id']}/revisions/{result['revision_id']}/checkout",
            headers=AUTH,
        ).status_code
        == 404
    )

    snapshots = client.get(
        f"/api/v1/item-revisions/{result['revision_id']}/content-snapshots", headers=AUTH
    )
    assert snapshots.status_code == 200, snapshots.text
    assert [row["sequence"] for row in snapshots.json()] == [2, 1]
    historical_artifacts = client.get(
        f"/api/v1/item-revisions/{result['revision_id']}/artifacts",
        params={"snapshot_id": result["content_snapshot_id"]},
        headers=AUTH,
    )
    assert historical_artifacts.status_code == 200, historical_artifacts.text
    assert [row["artifact"]["name"] for row in historical_artifacts.json()] == ["Bracket"]
    unknown_snapshot = client.get(
        f"/api/v1/item-revisions/{result['revision_id']}/artifacts",
        params={"snapshot_id": str(uuid.uuid4())},
        headers=AUTH,
    )
    assert unknown_snapshot.status_code == 404
    restore = client.post(
        f"/api/v1/staging/items/{result['item_id']}/revisions/{result['revision_id']}/edit",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "source_snapshot_id": result["content_snapshot_id"],
        },
    )
    assert restore.status_code == 201, restore.text
    assert restore.json()["base_snapshot_id"] == edit_result["content_snapshot_id"]
    assert (
        restore.json()["proposal"]["destination"]["base_snapshot_id"]
        == edit_result["content_snapshot_id"]
    )
    assert restore.json()["proposal"]["artifacts"][0]["name"] == "Bracket"
    assert client.delete(f"/api/v1/staging/{restore.json()['id']}", headers=AUTH).status_code == 204


def test_atomic_assembly_commit_creates_components_and_reuses_duplicate_revision():
    suffix = uuid.uuid4().hex
    reused_content = f"reused-{suffix}".encode()
    reused_hash = hashlib.sha256(reused_content).hexdigest()
    reused_storage_key = f"tests/{suffix}/reused.FCStd"
    storage = LocalStorageProvider(get_settings().storage_root)
    storage.create_upload(reused_storage_key).write_bytes(reused_content)
    storage.complete_upload(reused_storage_key)
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        reused_item = Item(
            part_number=f"REUSED-{suffix}",
            name="Reused component",
            part_type="PART",
            created_by=actor.id,
        )
        db.add(reused_item)
        db.flush()
        reused_revision = Revision(
            part_id=reused_item.id,
            revision_code="A",
            cad_system="FreeCAD",
            native_format="FCStd",
            package_sha256=reused_hash,
            state="READY",
            metadata_json={},
            created_by=actor.id,
        )
        db.add(reused_revision)
        db.flush()
        reused_version = WorkingVersion(
            revision_id=reused_revision.id,
            version_number=1,
            state="READY",
            source_sha256=reused_hash,
            metadata_json={},
            created_by=actor.id,
        )
        db.add(reused_version)
        db.flush()
        db.add(
            ArtifactFile(
                revision_id=reused_revision.id,
                working_version_id=reused_version.id,
                kind="NATIVE",
                storage_key=reused_storage_key,
                original_filename="reused.FCStd",
                mime_type="application/octet-stream",
                byte_size=len(reused_content),
                sha256=reused_hash,
                state="VERIFIED",
                required=True,
            )
        )
        reused_revision.current_working_version_id = reused_version.id
        reused_item.current_revision_id = reused_revision.id
        reused_item_id = reused_item.id
        reused_revision_id = reused_revision.id
        db.commit()

    contents = {
        "assembly.FCStd": f"assembly-{suffix}".encode(),
        "reused.FCStd": reused_content,
        "new.FCStd": f"new-{suffix}".encode(),
    }
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "operation": "CREATE_ITEM",
            "files": [
                {
                    "logical_path": name,
                    "filename": name,
                    "byte_size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "mime_type": "application/octet-stream",
                }
                for name, content in contents.items()
            ],
        },
    )
    assert created.status_code == 201, created.text
    staging = created.json()
    staged = {row["logical_path"]: row for row in staging["files"]}
    # The reused file is deliberately not uploaded. Its selected revision proves its content hash.
    for name in ("assembly.FCStd", "new.FCStd"):
        uploaded = client.put(
            staged[name]["upload_url"],
            headers={**AUTH, "Content-Type": "application/octet-stream"},
            content=contents[name],
        )
        assert uploaded.status_code == 200, uploaded.text

    # Preflight metadata must not suppress authoritative worker preview generation.
    with SessionLocal() as db:
        staging_row = db.get(StagingSession, uuid.UUID(staging["id"]))
        staging_row.metadata_json = local_freecad_metadata()
        db.commit()

    assembly_number = f"ASM-{suffix}"
    component_number = f"NEW-{suffix}"
    proposal = client.put(
        f"/api/v1/staging/{staging['id']}/proposal",
        headers=AUTH,
        json={
            "expected_snapshot_version": staging["snapshot_version"],
            "operation": "CREATE_ITEM",
            "item": {"item_number": assembly_number, "name": "Assembly", "item_type": "ASSEMBLY"},
            "revision": {"revision_code": "A", "cad_system": "FreeCAD", "native_format": "FCStd"},
            "artifacts": [
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "owner_staged_file_id": None,
                    "artifact_type": "CAD_ASSEMBLY",
                    "name": "Assembly",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": staged["assembly.FCStd"]["id"],
                            "reference_name": "MODEL",
                            "role": "PRIMARY",
                        },
                        {
                            "staged_file_id": staged["new.FCStd"]["id"],
                            "reference_name": "new.FCStd",
                            "role": "SECONDARY",
                        },
                        {
                            "staged_file_id": staged["reused.FCStd"]["id"],
                            "reference_name": "reused.FCStd",
                            "role": "SECONDARY",
                        },
                    ],
                },
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "owner_staged_file_id": staged["new.FCStd"]["id"],
                    "artifact_type": "CAD_MODEL",
                    "name": "New component",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": staged["new.FCStd"]["id"],
                            "reference_name": "MODEL",
                            "role": "PRIMARY",
                        }
                    ],
                },
            ],
            "identity_decisions": [
                {"staged_file_id": staged["assembly.FCStd"]["id"], "decision": "CREATE_NEW"},
                {
                    "staged_file_id": staged["reused.FCStd"]["id"],
                    "decision": "USE_EXISTING",
                    "existing_revision_id": str(reused_revision_id),
                },
                {
                    "staged_file_id": staged["new.FCStd"]["id"],
                    "decision": "CREATE_NEW",
                    "item": {
                        "item_number": component_number,
                        "name": "New component",
                        "item_type": "PART",
                    },
                    "revision": {
                        "revision_code": "A",
                        "cad_system": "FreeCAD",
                        "native_format": "FCStd",
                    },
                },
            ],
            "bom": [
                {
                    "parent_staged_file_id": staged["assembly.FCStd"]["id"],
                    "child_staged_file_id": staged["reused.FCStd"]["id"],
                    "instance_name": "reused",
                },
                {
                    "parent_staged_file_id": staged["assembly.FCStd"]["id"],
                    "child_staged_file_id": staged["reused.FCStd"]["id"],
                    "instance_name": "reused-2",
                },
                {
                    "parent_staged_file_id": staged["assembly.FCStd"]["id"],
                    "child_staged_file_id": staged["new.FCStd"]["id"],
                    "instance_name": "new",
                    "source_reference": "components/new.FCStd",
                    "reference_designator": "NewComponent001",
                    "transform": [
                        1,
                        0,
                        0,
                        12,
                        0,
                        1,
                        0,
                        34,
                        0,
                        0,
                        1,
                        56,
                        0,
                        0,
                        0,
                        1,
                    ],
                    "is_suppressed": True,
                    "is_visible": False,
                    "properties": {"assembly_model": "APP_LINK"},
                },
            ],
        },
    )
    assert proposal.status_code == 200, proposal.text
    version = proposal.json()["snapshot_version"]
    validation = client.post(f"/api/v1/staging/{staging['id']}/validate", headers=AUTH)
    assert validation.status_code == 200, validation.text
    assert validation.json()["status"] == "READY_WITH_WARNINGS"
    ready = client.post(
        f"/api/v1/staging/{staging['id']}/ready",
        headers=AUTH,
        json={"expected_snapshot_version": version},
    )
    assert ready.status_code == 200, ready.text
    committed = client.post(
        f"/api/v1/uploads/{staging['id']}/commit",
        headers=AUTH,
        json={"snapshot_version": version, "commit_id": str(uuid.uuid4())},
    )
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert len(result["created_item_ids"]) == 2
    assert len(result["created_revision_ids"]) == 2
    assert result["reused_revision_ids"] == [str(reused_revision_id)]
    with SessionLocal() as db:
        parent = db.get(Item, uuid.UUID(result["item_id"]))
        lines = list(
            db.scalars(
                select(BomLine).where(BomLine.parent_revision_id == parent.current_revision_id)
            )
        )
        original_bom_view_ids = {line.bom_view_revision_id for line in lines}
        assert {line.child_revision_id for line in lines} == {
            reused_revision_id,
            db.scalar(select(Item.current_revision_id).where(Item.part_number == component_number)),
        }
        assert db.get(Item, reused_item_id).part_number == f"REUSED-{suffix}"
        jobs = list(
            db.scalars(
                select(ProcessingJob).where(
                    ProcessingJob.revision_id.in_(
                        [uuid.UUID(value) for value in result["created_revision_ids"]]
                    )
                )
            )
        )
        assert len(jobs) == 2
        assert {job.type for job in jobs} == {"FCSTD_TO_MODELRYX"}

    bom = client.get(
        f"/api/v1/items/{result['item_id']}/revisions/{result['revision_id']}/bom",
        headers=AUTH,
    )
    assert bom.status_code == 200, bom.text
    assert {row["occurrence_key"] for row in bom.json()["occurrences"]} == {
        "new",
        "reused",
        "reused-2",
    }
    new_occurrence = next(
        row for row in bom.json()["occurrences"] if row["occurrence_key"] == "new"
    )
    assert new_occurrence["source_reference"] == "components/new.FCStd"
    assert new_occurrence["reference_designator"] == "NewComponent001"
    assert new_occurrence["transform"][3:12:4] == [12, 34, 56]
    assert new_occurrence["is_suppressed"] is True
    assert new_occurrence["is_visible"] is False
    assert new_occurrence["properties"] == {"assembly_model": "APP_LINK"}
    where_used = client.get(
        f"/api/v1/items/{reused_item_id}/revisions/{reused_revision_id}/where-used",
        headers=AUTH,
    )
    assert where_used.status_code == 200, where_used.text
    assert [row["parent_item"]["item_number"] for row in where_used.json()["items"]] == [
        assembly_number,
    ]
    assert where_used.json()["items"][0]["total_quantity"] == 2
    assert where_used.json()["items"][0]["occurrence_count"] == 2
    assert len(where_used.json()["items"][0]["paths"]) == 2
    child_relationships = client.get(
        f"/api/v1/items/{reused_item_id}/revisions/{reused_revision_id}/relationships",
        headers=AUTH,
    )
    assert child_relationships.status_code == 200, child_relationships.text
    referenced_by = [
        row for row in child_relationships.json() if row["direction"] == "referenced_by"
    ]
    assert len(referenced_by) == 1
    assert referenced_by[0]["item_number"] == assembly_number
    assert referenced_by[0]["occurrence_count"] == 2
    relationships = client.get(
        f"/api/v1/items/{result['item_id']}/revisions/{result['revision_id']}/relationships",
        headers=AUTH,
    )
    assert relationships.status_code == 200, relationships.text
    assert {row["item_number"] for row in relationships.json()} == {
        component_number,
        f"REUSED-{suffix}",
    }

    edit = client.post(
        f"/api/v1/staging/items/{result['item_id']}/revisions/{result['revision_id']}/edit",
        headers=AUTH,
        json={"client_staging_id": str(uuid.uuid4())},
    )
    assert edit.status_code == 201, edit.text
    edit_staging = edit.json()
    assert len(edit_staging["proposal"]["bom"]) == 3
    edit_proposal = edit_staging["proposal"]
    edit_proposal["bom"] = [
        line
        for line in edit_proposal["bom"]
        if line["child_revision_id"] != str(reused_revision_id)
    ]
    updated = client.put(
        f"/api/v1/staging/{edit_staging['id']}/proposal",
        headers=AUTH,
        json={**edit_proposal, "expected_snapshot_version": edit_staging["snapshot_version"]},
    )
    assert updated.status_code == 200, updated.text
    version = updated.json()["snapshot_version"]
    assert (
        client.post(
            f"/api/v1/staging/{edit_staging['id']}/validate",
            headers=AUTH,
        ).status_code
        == 200
    )
    ready = client.post(
        f"/api/v1/staging/{edit_staging['id']}/ready",
        headers=AUTH,
        json={"expected_snapshot_version": version},
    )
    assert ready.status_code == 200, ready.text
    edited = client.post(
        f"/api/v1/uploads/{edit_staging['id']}/commit",
        headers=AUTH,
        json={"snapshot_version": version, "commit_id": str(uuid.uuid4())},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["bom_view_revision_id"]
    with SessionLocal() as db:
        latest_lines = list(
            db.scalars(
                select(BomLine).where(
                    BomLine.bom_view_revision_id == uuid.UUID(edited.json()["bom_view_revision_id"])
                )
            )
        )
        assert len(latest_lines) == 1
        assert latest_lines[0].child_revision_id != reused_revision_id
        assert len(original_bom_view_ids) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(BomLine)
                .where(BomLine.bom_view_revision_id == next(iter(original_bom_view_ids)))
            )
            == 3
        )


def test_nested_external_assembly_commit_preserves_bom_and_package_ownership(
    example_worker,
) -> None:
    suffix = uuid.uuid4().hex
    contents = {
        "Suspension.assembly": f"root-{suffix}".encode(),
        "Components/front Upper Arm.assembly": f"nested-{suffix}".encode(),
        "Components/Front Upper Arm.part": f"arm-{suffix}".encode(),
        "Components/Pillow Ball.part": f"ball-{suffix}".encode(),
    }
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "operation": "CREATE_ITEM",
            "files": [
                {
                    "logical_path": path,
                    "filename": path.rsplit("/", 1)[-1],
                    "byte_size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "mime_type": "application/octet-stream",
                }
                for path, content in contents.items()
            ],
        },
    )
    assert created.status_code == 201, created.text
    staging = created.json()
    staged = {row["logical_path"]: row for row in staging["files"]}
    for path, content in contents.items():
        uploaded = client.put(
            staged[path]["upload_url"],
            headers={**AUTH, "Content-Type": "application/octet-stream"},
            content=content,
        )
        assert uploaded.status_code == 200, uploaded.text

    root_path = "Suspension.assembly"
    nested_path = "Components/front Upper Arm.assembly"
    part_paths = ["Components/Front Upper Arm.part", "Components/Pillow Ball.part"]
    root_number = f"ASM-ROOT-{suffix}"
    nested_number = f"ASM-NESTED-{suffix}"
    part_numbers = [f"PRT-ARM-{suffix}", f"PRT-BALL-{suffix}"]
    proposal = client.put(
        f"/api/v1/staging/{staging['id']}/proposal",
        headers=AUTH,
        json={
            "expected_snapshot_version": staging["snapshot_version"],
            "operation": "CREATE_ITEM",
            "item": {
                "item_number": root_number,
                "name": "Suspension",
                "item_type": "ASSEMBLY",
            },
            "revision": {
                "revision_code": "A",
                "cad_system": "Example CAD",
                "native_format": "assembly",
            },
            "artifacts": [
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "owner_staged_file_id": None,
                    "artifact_type": "CAD_ASSEMBLY",
                    "name": "Suspension",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": staged[path]["id"],
                            "reference_name": "MODEL" if path == root_path else path,
                            "role": "PRIMARY" if path == root_path else "SECONDARY",
                        }
                        for path in contents
                    ],
                },
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "owner_staged_file_id": staged[nested_path]["id"],
                    "artifact_type": "CAD_ASSEMBLY",
                    "name": "front Upper Arm",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": staged[path]["id"],
                            "reference_name": "MODEL" if path == nested_path else path,
                            "role": "PRIMARY" if path == nested_path else "SECONDARY",
                        }
                        for path in [nested_path, *part_paths]
                    ],
                },
                *[
                    {
                        "client_artifact_id": str(uuid.uuid4()),
                        "owner_staged_file_id": staged[path]["id"],
                        "artifact_type": "CAD_MODEL",
                        "name": path.rsplit("/", 1)[-1],
                        "classification": "AUTHORITATIVE",
                        "relation_type": "SPECIFICATION",
                        "is_primary": True,
                        "files": [
                            {
                                "staged_file_id": staged[path]["id"],
                                "reference_name": "MODEL",
                                "role": "PRIMARY",
                            }
                        ],
                    }
                    for path in part_paths
                ],
            ],
            "identity_decisions": [
                {"staged_file_id": staged[root_path]["id"], "decision": "CREATE_NEW"},
                {
                    "staged_file_id": staged[nested_path]["id"],
                    "decision": "CREATE_NEW",
                    "item": {
                        "item_number": nested_number,
                        "name": "front Upper Arm",
                        "item_type": "ASSEMBLY",
                    },
                    "revision": {
                        "revision_code": "A",
                        "cad_system": "Example CAD",
                        "native_format": "assembly",
                    },
                },
                *[
                    {
                        "staged_file_id": staged[path]["id"],
                        "decision": "CREATE_NEW",
                        "item": {
                            "item_number": number,
                            "name": path.rsplit("/", 1)[-1],
                            "item_type": "PART",
                        },
                        "revision": {
                            "revision_code": "A",
                            "cad_system": "Example CAD",
                            "native_format": "part",
                        },
                    }
                    for path, number in zip(part_paths, part_numbers, strict=True)
                ],
            ],
            "bom": [
                {
                    "parent_staged_file_id": staged[root_path]["id"],
                    "child_staged_file_id": staged[nested_path]["id"],
                    "instance_name": "front Upper Arm:1",
                },
                *[
                    {
                        "parent_staged_file_id": staged[nested_path]["id"],
                        "child_staged_file_id": staged[path]["id"],
                        "instance_name": f"{path.rsplit('/', 1)[-1]}:1",
                    }
                    for path in part_paths
                ],
            ],
        },
    )
    assert proposal.status_code == 200, proposal.text
    version = proposal.json()["snapshot_version"]
    validation = client.post(f"/api/v1/staging/{staging['id']}/validate", headers=AUTH)
    assert validation.status_code == 200, validation.text
    ready = client.post(
        f"/api/v1/staging/{staging['id']}/ready",
        headers=AUTH,
        json={"expected_snapshot_version": version},
    )
    assert ready.status_code == 200, ready.text
    committed = client.post(
        f"/api/v1/uploads/{staging['id']}/commit",
        headers=AUTH,
        json={"snapshot_version": version, "commit_id": str(uuid.uuid4())},
    )
    assert committed.status_code == 200, committed.text

    with SessionLocal() as db:
        root_item = db.scalar(select(Item).where(Item.part_number == root_number))
        nested_item = db.scalar(select(Item).where(Item.part_number == nested_number))
        assert root_item is not None and nested_item is not None
        root_view = db.scalar(
            select(BomViewRevision).where(
                BomViewRevision.revision_id == root_item.current_revision_id
            )
        )
        nested_view = db.scalar(
            select(BomViewRevision).where(
                BomViewRevision.revision_id == nested_item.current_revision_id
            )
        )
        assert root_view is not None and nested_view is not None
        root_lines = list(
            db.scalars(select(BomLine).where(BomLine.bom_view_revision_id == root_view.id))
        )
        nested_lines = list(
            db.scalars(select(BomLine).where(BomLine.bom_view_revision_id == nested_view.id))
        )
        assert [line.child_revision_id for line in root_lines] == [nested_item.current_revision_id]
        assert {line.occurrence_key for line in nested_lines} == {
            "Front Upper Arm.part:1",
            "Pillow Ball.part:1",
        }
        nested_files = set(
            db.scalars(
                select(ArtifactFile.original_filename).where(
                    ArtifactFile.revision_id == nested_item.current_revision_id
                )
            )
        )
        assert nested_files == {
            "front Upper Arm.assembly",
            "Front Upper Arm.part",
            "Pillow Ball.part",
        }
