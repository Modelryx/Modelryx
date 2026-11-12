import hashlib
import uuid

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.db import SessionLocal
from modelryx.models import (
    Artifact,
    ArtifactFile,
    AuditLog,
    BomLine,
    BomViewRevision,
    Item,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
)
from sqlalchemy import select

client = TestClient(app)


def _item_with_file() -> tuple[dict, str]:
    content = f"purge-{uuid.uuid4()}".encode()
    item_number = f"PURGE-{uuid.uuid4().hex}"
    staged = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "files": [
                {
                    "logical_path": "purge.FCStd",
                    "filename": "purge.FCStd",
                    "mime_type": "application/octet-stream",
                    "byte_size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ],
        },
    ).json()
    staged_file = staged["files"][0]
    response = client.put(
        staged_file["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert response.status_code == 200
    proposal = client.put(
        f"/api/v1/staging/{staged['id']}/proposal",
        headers=AUTH,
        json={
            "expected_snapshot_version": staged["snapshot_version"],
            "operation": "CREATE_ITEM",
            "item": {
                "item_number": item_number,
                "name": "Disposable test item",
                "item_type": "PART",
            },
            "revision": {"revision_code": "A", "cad_system": "FreeCAD", "native_format": "FCStd"},
            "artifacts": [
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "artifact_type": "CAD_MODEL",
                    "name": "Disposable model",
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
    ).json()
    assert client.post(f"/api/v1/staging/{staged['id']}/validate", headers=AUTH).status_code == 200
    ready = client.post(
        f"/api/v1/staging/{staged['id']}/ready",
        headers=AUTH,
        json={
            "expected_snapshot_version": proposal["snapshot_version"],
        },
    ).json()
    committed = client.post(
        f"/api/v1/uploads/{staged['id']}/commit",
        headers=AUTH,
        json={
            "snapshot_version": ready["snapshot_version"],
            "commit_id": str(uuid.uuid4()),
        },
    ).json()
    item = client.get(f"/api/v1/items/{committed['item_id']}", headers=AUTH).json()
    with SessionLocal() as db:
        file_id = db.scalar(
            select(ArtifactFile.id).where(
                ArtifactFile.artifact_id == uuid.UUID(committed["artifact_ids"][0])
            )
        )
        assert file_id is not None
    return item, str(file_id)


def test_admin_can_preview_and_permanently_purge_specific_item() -> None:
    item, _ = _item_with_file()
    preview = client.get(f"/api/v1/admin/items/{item['id']}/purge", headers=AUTH)
    assert preview.status_code == 200
    assert preview.json()["revision_count"] == 1
    assert preview.json()["artifact_count"] == 1
    assert preview.json()["stored_bytes"] > 0

    mismatch = client.post(
        f"/api/v1/admin/items/{item['id']}/purge",
        headers=AUTH,
        json={"item_number": "WRONG"},
    )
    assert mismatch.status_code == 422

    deleted = client.post(
        f"/api/v1/admin/items/{item['id']}/purge",
        headers=AUTH,
        json={"item_number": item["item_number"]},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(f"/api/v1/items/{item['id']}", headers=AUTH).status_code == 404

    with SessionLocal() as db:
        assert db.get(Item, uuid.UUID(item["id"])) is None
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.entity_id == uuid.UUID(item["id"]),
                AuditLog.action == "item.purged",
            )
        )
        assert audit is not None
        assert audit.details_json["deletion"] == "permanent"


def test_admin_can_delete_only_mutable_revision_and_current_pointer_is_repaired() -> None:
    item, _ = _item_with_file()
    item_id = uuid.UUID(item["id"])
    original_revision_id = uuid.UUID(item["current_revision_id"])
    with SessionLocal() as db:
        original = db.get(Revision, original_revision_id)
        replacement = Revision(
            part_id=item_id,
            revision_code="B",
            description="Temporary revision",
            cad_system=original.cad_system,
            native_format=original.native_format,
            metadata_json={},
            search_text="",
            state="DRAFT",
            lifecycle_state="WORKING",
            metadata_status="UNAVAILABLE",
            created_by=original.created_by,
        )
        db.add(replacement)
        db.flush()
        owner = db.get(Item, item_id)
        owner.current_revision_id = replacement.id
        replacement_id = replacement.id
        db.commit()

    options = client.get(
        f"/api/v1/admin/items/{item['id']}/revisions/deletion-options", headers=AUTH
    )
    assert options.status_code == 200, options.text
    option = next(row for row in options.json() if row["id"] == str(replacement_id))
    assert option["eligible"] is True
    assert option["is_current"] is True

    mismatch = client.post(
        f"/api/v1/admin/items/{item['id']}/revisions/{replacement_id}/delete",
        headers=AUTH,
        json={"revision_code": "WRONG"},
    )
    assert mismatch.status_code == 422
    deleted = client.post(
        f"/api/v1/admin/items/{item['id']}/revisions/{replacement_id}/delete",
        headers=AUTH,
        json={"revision_code": "B"},
    )
    assert deleted.status_code == 204, deleted.text
    with SessionLocal() as db:
        assert db.get(Revision, replacement_id) is None
        assert db.get(Item, item_id).current_revision_id == original_revision_id


def test_admin_revision_deletion_protects_released_history() -> None:
    item, _ = _item_with_file()
    revision_id = uuid.UUID(item["current_revision_id"])
    with SessionLocal() as db:
        revision = db.get(Revision, revision_id)
        revision.lifecycle_state = "RELEASED"
        db.commit()

    options = client.get(
        f"/api/v1/admin/items/{item['id']}/revisions/deletion-options", headers=AUTH
    )
    assert options.status_code == 200
    option = next(row for row in options.json() if row["id"] == str(revision_id))
    assert option["eligible"] is False
    assert "immutable" in option["reason"]
    deleted = client.post(
        f"/api/v1/admin/items/{item['id']}/revisions/{revision_id}/delete",
        headers=AUTH,
        json={"revision_code": option["revision_code"]},
    )
    assert deleted.status_code == 409


def test_admin_revision_deletion_protects_active_attachment_edit() -> None:
    item, _ = _item_with_file()
    revision_id = item["current_revision_id"]
    edit_endpoint = (
        f"/api/v1/items/{item['id']}/revisions/{revision_id}/attachment-edit"
    )
    started = client.post(edit_endpoint, headers=AUTH)
    assert started.status_code == 201, started.text

    options = client.get(
        f"/api/v1/admin/items/{item['id']}/revisions/deletion-options", headers=AUTH
    )
    assert options.status_code == 200
    option = next(row for row in options.json() if row["id"] == revision_id)
    assert option["eligible"] is False
    assert option["reason"] == "Revision has an active workspace task"

    assert client.delete(f"{edit_endpoint}?cancel=true", headers=AUTH).status_code == 204


def test_purge_removes_checkout_staging_that_references_revision_snapshot() -> None:
    item, _ = _item_with_file()
    revision_id = item["current_revision_id"]
    staging = client.post(
        f"/api/v1/staging/items/{item['id']}/revisions/{revision_id}/edit",
        headers=AUTH,
        json={"client_staging_id": str(uuid.uuid4())},
    )
    assert staging.status_code == 201, staging.text

    deleted = client.post(
        f"/api/v1/admin/items/{item['id']}/purge",
        headers=AUTH,
        json={"item_number": item["item_number"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True


def test_purge_deletes_owned_derived_artifacts_before_their_source() -> None:
    item, source_file_id = _item_with_file()
    with SessionLocal() as db:
        source_file = db.get(ArtifactFile, uuid.UUID(source_file_id))
        source = db.get(Artifact, source_file.artifact_id)
        owner = db.get(Item, uuid.UUID(item["id"]))
        derived = Artifact(
            artifact_type="GLB",
            name="Derived preview",
            classification="DERIVED",
            source="WORKER",
            state="READY",
            derived_from_artifact_id=source.id,
            created_by=owner.created_by,
        )
        db.add(derived)
        db.flush()
        db.add(
            RevisionArtifactRelation(
                revision_id=source_file.revision_id,
                artifact_id=derived.id,
                relation_type="REPRESENTATION",
                is_primary=False,
                created_by=owner.created_by,
            )
        )
        db.commit()
        source_id = source.id
        derived_id = derived.id

    deleted = client.post(
        f"/api/v1/admin/items/{item['id']}/purge",
        headers=AUTH,
        json={"item_number": item["item_number"], "cascade_item_numbers": []},
    )
    assert deleted.status_code == 200, deleted.text
    with SessionLocal() as db:
        assert db.get(Artifact, source_id) is None
        assert db.get(Artifact, derived_id) is None


def test_purge_promotes_a_retained_legacy_child_artifact() -> None:
    root, root_file_id = _item_with_file()
    child, child_file_id = _item_with_file()
    with SessionLocal() as db:
        root_file = db.get(ArtifactFile, uuid.UUID(root_file_id))
        root_source = db.get(Artifact, root_file.artifact_id)
        root_item = db.get(Item, uuid.UUID(root["id"]))
        child_file = db.get(ArtifactFile, uuid.UUID(child_file_id))
        child_revision = db.get(Revision, child_file.revision_id)
        child_revision.derived_metadata_json = {
            "inventorComponent": {"sourceRevisionId": str(root_file.revision_id)}
        }
        retained = Artifact(
            artifact_type="GLB",
            name="Retained component preview",
            classification="DERIVED",
            source="WORKER",
            state="READY",
            derived_from_artifact_id=root_source.id,
            created_by=root_item.created_by,
        )
        db.add(retained)
        db.flush()
        db.add(
            RevisionArtifactRelation(
                revision_id=child_revision.id,
                artifact_id=retained.id,
                relation_type="REPRESENTATION",
                is_primary=False,
                created_by=root_item.created_by,
            )
        )
        db.commit()
        retained_id = retained.id

    preview = client.get(f"/api/v1/admin/items/{root['id']}/purge", headers=AUTH)
    assert preview.status_code == 200, preview.text
    assert child["item_number"] in preview.json()["cascade_item_numbers"]
    deleted = client.post(
        f"/api/v1/admin/items/{root['id']}/purge",
        headers=AUTH,
        json={"item_number": root["item_number"], "cascade_item_numbers": []},
    )
    assert deleted.status_code == 200, deleted.text
    with SessionLocal() as db:
        retained_after = db.get(Artifact, retained_id)
        assert retained_after is not None
        assert retained_after.classification == "AUTHORITATIVE"
        assert retained_after.derived_from_artifact_id is None
        assert db.get(Item, uuid.UUID(child["id"])) is not None


def test_purge_is_blocked_when_an_artifact_is_shared_by_another_item() -> None:
    target, target_file_id = _item_with_file()
    owner, owner_file_id = _item_with_file()
    with SessionLocal() as db:
        target_file = db.get(ArtifactFile, uuid.UUID(target_file_id))
        owner_file = db.get(ArtifactFile, uuid.UUID(owner_file_id))
        target_item = db.get(Item, uuid.UUID(target["id"]))
        container = Artifact(
            artifact_type="CAD_MODEL",
            name="Shared model",
            classification="AUTHORITATIVE",
            source="USER",
            state="READY",
            created_by=target_item.created_by,
        )
        db.add(container)
        db.flush()
        target_file.artifact_id = container.id
        target_file.reference_name = "MODEL"
        target_file.role = "PRIMARY"
        db.add_all(
            [
                RevisionArtifactRelation(
                    revision_id=target_file.revision_id,
                    artifact_id=container.id,
                    relation_type="SPECIFICATION",
                    is_primary=True,
                    created_by=target_item.created_by,
                ),
                RevisionArtifactRelation(
                    revision_id=owner_file.revision_id,
                    artifact_id=container.id,
                    relation_type="REFERENCE",
                    is_primary=False,
                    created_by=target_item.created_by,
                ),
            ]
        )
        db.commit()

    preview = client.get(f"/api/v1/admin/items/{target['id']}/purge", headers=AUTH)
    assert preview.status_code == 200, preview.text
    assert preview.json()["blocking_reference_count"] == 1
    assert preview.json()["blocking_item_numbers"] == [owner["item_number"]]
    deleted = client.post(
        f"/api/v1/admin/items/{target['id']}/purge",
        headers=AUTH,
        json={"item_number": target["item_number"]},
    )
    assert deleted.status_code == 409, deleted.text
    with SessionLocal() as db:
        assert db.get(ArtifactFile, uuid.UUID(target_file_id)) is not None


def test_current_bom_child_without_legacy_metadata_is_offered_for_cascade_deletion() -> None:
    root, _ = _item_with_file()
    child, _ = _item_with_file()
    with SessionLocal() as db:
        root_item = db.get(Item, uuid.UUID(root["id"]))
        child_item = db.get(Item, uuid.UUID(child["id"]))
        root_revision = db.get(Revision, root_item.current_revision_id)
        child_revision = db.get(Revision, child_item.current_revision_id)
        root_snapshot = db.scalar(
            select(RevisionContentSnapshot).where(
                RevisionContentSnapshot.revision_id == root_revision.id
            )
        )
        view = BomViewRevision(
            revision_id=root_revision.id,
            snapshot_id=root_snapshot.id,
            sequence=1,
            created_by=root_item.created_by,
        )
        db.add(view)
        db.flush()
        db.add(
            BomLine(
                bom_view_revision_id=view.id,
                parent_revision_id=root_revision.id,
                child_revision_id=child_revision.id,
                occurrence_key="ordinary-child:1",
                quantity=1,
                sequence=0,
            )
        )
        db.commit()

    preview = client.get(f"/api/v1/admin/items/{root['id']}/purge", headers=AUTH)
    assert preview.status_code == 200, preview.text
    assert preview.json()["cascade_item_numbers"] == [child["item_number"]]
    assert preview.json()["protected_legacy_item_numbers"] == []


def test_nested_inventor_components_with_stale_source_are_offered_for_cascade_deletion() -> None:
    root, _ = _item_with_file()
    with SessionLocal() as db:
        root_item = db.get(Item, uuid.UUID(root["id"]))
        root_revision = db.get(Revision, root_item.current_revision_id)
        root_snapshot = db.scalar(
            select(RevisionContentSnapshot).where(
                RevisionContentSnapshot.revision_id == root_revision.id
            )
        )
        subassembly = Item(
            part_number=f"SUB-{uuid.uuid4().hex}",
            name="Generated nested assembly",
            category="Inventor Component",
            part_type="ASSEMBLY",
            created_by=root_item.created_by,
        )
        part = Item(
            part_number=f"PART-{uuid.uuid4().hex}",
            name="Generated nested part",
            category="Inventor Component",
            part_type="PART",
            created_by=root_item.created_by,
        )
        db.add_all([subassembly, part])
        db.flush()

        subassembly_revision = Revision(
            part_id=subassembly.id,
            revision_code="A-PF1",
            cad_system="Inventor",
            native_format="IAM",
            metadata_json={},
            derived_metadata_json={
                "inventorComponent": {"sourceRevisionId": str(uuid.uuid4())}
            },
            state="READY",
            created_by=root_item.created_by,
        )
        part_revision = Revision(
            part_id=part.id,
            revision_code="A-PF1",
            cad_system="Inventor",
            native_format="IPT",
            metadata_json={},
            derived_metadata_json={
                "inventorComponent": {"sourceRevisionId": str(uuid.uuid4())}
            },
            state="READY",
            created_by=root_item.created_by,
        )
        db.add_all([subassembly_revision, part_revision])
        db.flush()
        subassembly.current_revision_id = subassembly_revision.id
        part.current_revision_id = part_revision.id

        subassembly_snapshot = RevisionContentSnapshot(
            revision_id=subassembly_revision.id,
            sequence=1,
            commit_id=uuid.uuid4(),
            created_by=root_item.created_by,
        )
        part_snapshot = RevisionContentSnapshot(
            revision_id=part_revision.id,
            sequence=1,
            commit_id=uuid.uuid4(),
            created_by=root_item.created_by,
        )
        db.add_all([subassembly_snapshot, part_snapshot])
        db.flush()
        root_view = BomViewRevision(
            revision_id=root_revision.id,
            snapshot_id=root_snapshot.id,
            sequence=1,
            created_by=root_item.created_by,
        )
        nested_view = BomViewRevision(
            revision_id=subassembly_revision.id,
            snapshot_id=subassembly_snapshot.id,
            sequence=1,
            created_by=root_item.created_by,
        )
        db.add_all([root_view, nested_view])
        db.flush()
        db.add_all(
            [
                BomLine(
                    bom_view_revision_id=root_view.id,
                    parent_revision_id=root_revision.id,
                    child_revision_id=subassembly_revision.id,
                    occurrence_key="subassembly:1",
                    quantity=1,
                    sequence=0,
                ),
                BomLine(
                    bom_view_revision_id=nested_view.id,
                    parent_revision_id=subassembly_revision.id,
                    child_revision_id=part_revision.id,
                    occurrence_key="part:1",
                    quantity=1,
                    sequence=0,
                ),
            ]
        )
        db.commit()
        expected = sorted([subassembly.part_number, part.part_number])

    preview = client.get(f"/api/v1/admin/items/{root['id']}/purge", headers=AUTH)
    assert preview.status_code == 200, preview.text
    assert sorted(preview.json()["cascade_item_numbers"]) == expected
    assert preview.json()["protected_legacy_item_numbers"] == []
