import hashlib
import uuid

from artifact_helpers import commit_item
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    ArtifactFile,
    ContentBlob,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
)
from modelryx.services.additional_files import inspect_additional_files
from sqlalchemy import select

client = TestClient(app)


def test_pdf_preview_is_verified_version_scoped_and_authenticated(monkeypatch) -> None:
    item = ready_item()
    for content, expected in [
        (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n", 200),
        (b"not a PDF", 415),
    ]:
        response = client.post(
            f"/api/v1/items/{item['id']}/attachments",
            headers=AUTH,
            json={
                "client_version_id": str(uuid.uuid4()),
                "name": "PDF preview test",
                "attachment_type": "DRAWING",
                "filename": "drawing.pdf",
                "mime_type": "application/pdf",
                "byte_size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        )
        assert response.status_code == 201, response.text
        version = response.json()["versions"][0]
        url = f"/api/v1/attachment-versions/{version['id']}/preview"
        assert client.get(url, headers=AUTH).status_code == 404
        uploaded = client.put(
            version["upload_url"],
            headers={**AUTH, "Content-Type": "application/pdf"},
            content=content,
        )
        assert uploaded.status_code == 200, uploaded.text
        assert client.get(url).status_code == 401
        preview = client.get(url, headers=AUTH)
        assert preview.status_code == expected, preview.text
        if expected == 200:
            assert preview.content == content
            assert preview.headers["content-type"] == "application/pdf"
            assert preview.headers["content-disposition"].startswith("inline;")
            assert preview.headers["cache-control"] == "private, no-store"
            with monkeypatch.context() as patch:
                patch.setattr(get_settings(), "max_pdf_preview_bytes", 1)
                assert client.get(url, headers=AUTH).status_code == 413


def ready_item() -> dict:
    content = b"attachment-test-fcstd"
    result = commit_item(
        client,
        item_number=f"ITEM-{uuid.uuid4().hex}",
        name="Attachment test item",
        files=[("item.FCStd", content)],
    )
    response = client.get(f"/api/v1/items/{result['item_id']}", headers=AUTH)
    assert response.status_code == 200
    return response.json()


def test_revision_attachment_edit_creates_resumable_workspace_task() -> None:
    item = ready_item()
    revision_id = item["current_revision_id"]
    endpoint = f"/api/v1/items/{item['id']}/revisions/{revision_id}/attachment-edit"

    initial = client.get(endpoint, headers=AUTH)
    assert initial.status_code == 200
    assert initial.json()["active"] is False

    started = client.post(endpoint, headers=AUTH)
    assert started.status_code == 201, started.text
    task_id = started.json()["task_id"]
    assert started.json()["revision_id"] == revision_id

    resumed = client.post(endpoint, headers=AUTH)
    assert resumed.status_code == 201
    assert resumed.json()["task_id"] == task_id

    staged_ids = []
    for index in range(2):
        content = f"pending attachment {index}".encode()
        staged = client.post(
            f"/api/v1/items/{item['id']}/additional-files/staging",
            headers=AUTH,
            json={
                "client_staging_id": str(uuid.uuid4()),
                "name": f"Pending attachment {index}",
                "attachment_type": "OTHER",
                "filename": f"pending-{index}.bin",
                "mime_type": "application/octet-stream",
                "byte_size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "scope": "REVISION",
                "revision_id": revision_id,
            },
        )
        assert staged.status_code == 201, staged.text
        staged_ids.append(staged.json()["id"])
    listed_stages = client.get(
        f"/api/v1/items/{item['id']}/additional-files/staging"
        f"?scope=REVISION&revision_id={revision_id}", headers=AUTH
    )
    assert {stage["id"] for stage in listed_stages.json()} == set(staged_ids)
    for stage_id in staged_ids:
        assert client.delete(
            f"/api/v1/additional-files/staging/{stage_id}", headers=AUTH
        ).status_code == 204

    workspace = client.get("/api/v1/me/workspace", headers=AUTH)
    assert workspace.status_code == 200
    task = next(row for row in workspace.json()["uploads"] if row["id"] == task_id)
    assert task["operation"] == "EDIT_REVISION_ATTACHMENT"
    assert task["item_id"] == item["id"]
    assert task["revision_id"] == revision_id
    assert task["is_finished"] is False

    finished = client.delete(endpoint, headers=AUTH)
    assert finished.status_code == 204, finished.text
    assert client.get(endpoint, headers=AUTH).json()["active"] is False
    completed = client.get("/api/v1/me/workspace", headers=AUTH).json()["uploads"]
    completed_task = next(row for row in completed if row["id"] == task_id)
    assert completed_task["is_finished"] is True

    item_endpoint = f"/api/v1/items/{item['id']}/attachment-edit"
    item_started = client.post(item_endpoint, headers=AUTH)
    assert item_started.status_code == 201, item_started.text
    item_task_id = item_started.json()["task_id"]
    assert item_started.json()["revision_id"] is None
    assert client.get(item_endpoint, headers=AUTH).json()["active"] is True
    assert client.get(endpoint, headers=AUTH).json()["active"] is False
    item_tasks = client.get("/api/v1/me/workspace", headers=AUTH).json()["uploads"]
    item_task = next(row for row in item_tasks if row["id"] == item_task_id)
    assert item_task["operation"] == "EDIT_ITEM_ATTACHMENT"
    assert item_task["revision_id"] is None
    assert client.delete(f"{item_endpoint}?cancel=true", headers=AUTH).status_code == 204
    remaining_tasks = client.get("/api/v1/me/workspace", headers=AUTH).json()["uploads"]
    assert not any(row["id"] == item_task_id for row in remaining_tasks)


def test_item_terminology_and_versioned_drawing_attachment() -> None:
    item = ready_item()
    assert item["item_type"] == "PART"
    assert "part_number" not in item

    drawing = b"%PDF-test-drawing"
    client_version_id = str(uuid.uuid4())
    created = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        headers=AUTH,
        json={
            "client_version_id": client_version_id,
            "name": "Manufacturing drawing",
            "attachment_type": "DRAWING",
            "filename": "drawing.pdf",
            "mime_type": "application/pdf",
            "byte_size": len(drawing),
            "sha256": hashlib.sha256(drawing).hexdigest(),
            "scope": "REVISION",
            "revision_id": item["current_revision_id"],
        },
    )
    assert created.status_code == 201
    attachment = created.json()
    version = attachment["versions"][0]
    assert version["version_number"] == 1
    assert version["client_version_id"] == client_version_id
    assert version["state"] == "UPLOADING"
    assert version["scope"] == "REVISION"
    assert version["revision_id"] == item["current_revision_id"]

    uploaded = client.put(
        version["upload_url"],
        headers={**AUTH, "Content-Type": "application/pdf"},
        content=drawing,
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["state"] == "READY"
    downloaded = client.get(uploaded.json()["download_url"], headers=AUTH)
    assert downloaded.status_code == 200
    assert downloaded.content == drawing

    item_attachments = client.get(
        f"/api/v1/items/{item['id']}/attachments", headers=AUTH
    )
    assert item_attachments.status_code == 200
    assert all(row["id"] != attachment["id"] for row in item_attachments.json())

    current_item = client.get(f"/api/v1/items/{item['id']}", headers=AUTH).json()
    revision_attachments = client.get(
        f"/api/v1/items/{item['id']}/revisions/{current_item['current_revision_id']}/attachments",
        headers=AUTH,
    )
    assert revision_attachments.status_code == 200
    assert revision_attachments.json()[0]["latest_ready_version"]["sha256"] == version["sha256"]
    with SessionLocal() as db:
        relation = db.scalar(select(RevisionArtifactRelation).where(
            RevisionArtifactRelation.revision_id == uuid.UUID(item["current_revision_id"]),
            RevisionArtifactRelation.artifact_id == uuid.UUID(version["id"]),
            RevisionArtifactRelation.relation_type == "ATTACHMENT",
        ))
        assert relation is not None
        latest_snapshot = db.scalar(
            select(RevisionContentSnapshot)
            .where(RevisionContentSnapshot.revision_id == uuid.UUID(item["current_revision_id"]))
            .order_by(RevisionContentSnapshot.sequence.desc())
        )
        assert latest_snapshot is not None
        assert db.scalar(select(RevisionContentSnapshotArtifact).where(
            RevisionContentSnapshotArtifact.snapshot_id == latest_snapshot.id,
            RevisionContentSnapshotArtifact.relation_id == relation.id,
        )) is not None

    revision_history = client.get(
        f"/api/v1/items/{item['id']}/revisions/{current_item['current_revision_id']}/history",
        headers=AUTH,
    )
    attachment_event = next(
        entry for entry in revision_history.json() if entry["action"] == "attachment.added"
    )
    assert attachment_event["details"]["filename"] == "drawing.pdf"
    assert attachment_event["details"]["version"] == 1

    retried = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        headers=AUTH,
        json={
            "client_version_id": client_version_id,
            "name": "Ignored retry name",
            "attachment_type": "DRAWING",
            "filename": "drawing.pdf",
            "mime_type": "application/pdf",
            "byte_size": len(drawing),
            "sha256": hashlib.sha256(drawing).hexdigest(),
        },
    )
    assert retried.status_code == 201
    assert retried.json()["id"] == attachment["id"]
    assert len(retried.json()["versions"]) == 1

    unchanged = client.post(
        f"/api/v1/attachments/{attachment['id']}/versions",
        headers=AUTH,
        json={
            "filename": "drawing-copy.pdf",
            "mime_type": "application/pdf",
            "byte_size": len(drawing),
            "sha256": hashlib.sha256(drawing).hexdigest(),
        },
    )
    assert unchanged.status_code == 201
    assert len(unchanged.json()["versions"]) == 1

    second_drawing = b"%PDF-test-drawing-version-2"
    second = client.post(
        f"/api/v1/attachments/{attachment['id']}/versions",
        headers=AUTH,
        json={
            "filename": "drawing-b.pdf",
            "mime_type": "application/pdf",
            "byte_size": len(second_drawing),
            "sha256": hashlib.sha256(second_drawing).hexdigest(),
            "comment": "Updated dimensions",
        },
    )
    assert second.status_code == 201
    assert [row["version_number"] for row in second.json()["versions"]] == [1, 2]


def test_additional_file_preflight_classifies_exact_and_name_collisions() -> None:
    item = ready_item()
    content = b"%PDF-additional-file-preflight"
    digest = hashlib.sha256(content).hexdigest()
    created = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        headers=AUTH,
        json={
            "name": "Material specification",
            "attachment_type": "SPECIFICATION",
            "filename": "material-spec.pdf",
            "mime_type": "application/pdf",
            "byte_size": len(content),
            "sha256": digest,
        },
    )
    assert created.status_code == 201, created.text
    attachment = created.json()
    version = attachment["versions"][0]
    uploaded = client.put(
        version["upload_url"],
        headers={**AUTH, "Content-Type": "application/pdf"},
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["scope"] == "ITEM"
    revision_files = client.get(
        f"/api/v1/items/{item['id']}/revisions/{item['current_revision_id']}/attachments",
        headers=AUTH,
    )
    assert revision_files.status_code == 200, revision_files.text
    assert revision_files.json() == []

    base_payload = {
        "name": "Material specification",
        "filename": "material-spec.pdf",
        "mime_type": "application/pdf",
        "byte_size": len(content),
        "sha256": digest,
    }
    exact_current = client.post(
        f"/api/v1/items/{item['id']}/additional-files/preflight",
        headers=AUTH,
        json={**base_payload, "attachment_id": attachment["id"]},
    )
    assert exact_current.status_code == 200, exact_current.text
    assert exact_current.json()["classification"] == "EXACT_CURRENT_VERSION"
    assert exact_current.json()["upload_required"] is False
    assert exact_current.json()["candidates"][0]["attachment_id"] == attachment["id"]
    assert exact_current.json()["candidates"][0]["scope"] == "ITEM"

    sha_only_match = client.post(
        f"/api/v1/items/{item['id']}/additional-files/preflight",
        headers=AUTH,
        json={**base_payload, "name": "SHA-only match", "byte_size": len(content) + 1},
    )
    assert sha_only_match.status_code == 200, sha_only_match.text
    assert sha_only_match.json()["classification"] == "EXACT_ITEM_MATCH"

    exact_item = client.post(
        f"/api/v1/items/{item['id']}/additional-files/preflight",
        headers=AUTH,
        json={**base_payload, "name": "Supplier copy"},
    )
    assert exact_item.status_code == 200, exact_item.text
    assert exact_item.json()["classification"] == "EXACT_ITEM_MATCH"
    assert exact_item.json()["reusable_content"] is True

    different = b"%PDF-different-content"
    same_name = client.post(
        f"/api/v1/items/{item['id']}/additional-files/preflight",
        headers=AUTH,
        json={
            **base_payload,
            "byte_size": len(different),
            "sha256": hashlib.sha256(different).hexdigest(),
        },
    )
    assert same_name.status_code == 200, same_name.text
    assert same_name.json()["classification"] == "SAME_NAME_DIFFERENT_CONTENT"
    assert same_name.json()["upload_required"] is True

    second_item = ready_item()
    global_match = client.post(
        f"/api/v1/items/{second_item['id']}/additional-files/preflight",
        headers=AUTH,
        json=base_payload,
    )
    assert global_match.status_code == 200, global_match.text
    assert global_match.json()["classification"] == "EXACT_GLOBAL_MATCH"
    assert global_match.json()["candidates"] == []

    reused = client.post(
        f"/api/v1/items/{second_item['id']}/attachments",
        headers=AUTH,
        json={
            **base_payload,
            "name": "Supplier material specification",
            "attachment_type": "SPECIFICATION",
            "duplicate_action": "REUSE_EXISTING",
        },
    )
    assert reused.status_code == 201, reused.text
    reused_version = reused.json()["latest_ready_version"]
    assert reused_version["state"] == "READY"
    assert reused_version["upload_url"] is None
    reused_download = client.get(reused_version["download_url"], headers=AUTH)
    assert reused_download.status_code == 200
    assert reused_download.content == content

    with SessionLocal() as db:
        original_file = db.scalar(
            select(ArtifactFile).where(ArtifactFile.artifact_id == version["id"])
        )
        reused_file = db.scalar(
            select(ArtifactFile).where(ArtifactFile.artifact_id == reused_version["id"])
        )
        assert original_file.content_blob_id == reused_file.content_blob_id
        assert len(list(db.scalars(select(ContentBlob).where(
            ContentBlob.sha256 == digest,
            ContentBlob.byte_size == len(content),
        )))) == 1

    new_content = client.post(
        f"/api/v1/items/{second_item['id']}/additional-files/preflight",
        headers=AUTH,
        json={
            **base_payload,
            "name": "Unique report",
            "byte_size": 4,
            "sha256": hashlib.sha256(b"new!").hexdigest(),
        },
    )
    assert new_content.status_code == 200, new_content.text
    assert new_content.json()["classification"] == "NEW_CONTENT"

    unavailable_reuse = client.post(
        f"/api/v1/items/{second_item['id']}/attachments",
        headers=AUTH,
        json={
            "name": "Unavailable content",
            "attachment_type": "OTHER",
            "filename": "missing.bin",
            "mime_type": "application/octet-stream",
            "byte_size": 4,
            "sha256": hashlib.sha256(b"none").hexdigest(),
            "duplicate_action": "REUSE_EXISTING",
        },
    )
    assert unavailable_reuse.status_code == 409

    with SessionLocal() as db:
        report = inspect_additional_files(db)
    assert report.attachments >= 1
    assert report.ready_versions >= 1
    assert not any(
        finding.code == "LEGACY_REVISION_RELATION_MISSING"
        and finding.version_id == version["id"]
        for finding in report.findings
    )


def test_item_additional_file_staging_commit_history_archive_and_stale_guard() -> None:
    item = ready_item()
    content = b"staged item-level additional file"
    manifest = {
        "client_staging_id": str(uuid.uuid4()),
        "name": "Manufacturing notes",
        "attachment_type": "OTHER",
        "filename": "notes.bin",
        "mime_type": "application/octet-stream",
        "byte_size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    created = client.post(
        f"/api/v1/items/{item['id']}/additional-files/staging",
        headers=AUTH,
        json=manifest,
    )
    assert created.status_code == 201, created.text
    stage = created.json()
    assert stage["action"] == "CREATE"
    assert stage["status"] == "DRAFT"

    uploaded = client.put(
        stage["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["status"] == "READY"

    commit_id = str(uuid.uuid4())
    committed = client.post(
        f"/api/v1/additional-files/staging/{stage['id']}/commit",
        headers=AUTH,
        json={"commit_id": commit_id},
    )
    assert committed.status_code == 200, committed.text
    result = committed.json()["result"]
    attachment_id = result["attachment_id"]
    assert result["version_number"] == 1
    listed_after_commit = client.get(
        f"/api/v1/items/{item['id']}/attachments", headers=AUTH
    )
    assert listed_after_commit.status_code == 200
    assert listed_after_commit.json()[0]["checkout_status"] == "AVAILABLE"
    checked_out = client.post(
        f"/api/v1/attachments/{attachment_id}/checkout", headers=AUTH
    )
    assert checked_out.status_code == 201
    assert checked_out.json()["attachment_id"] == attachment_id
    listed_while_checked_out = client.get(
        f"/api/v1/items/{item['id']}/attachments", headers=AUTH
    )
    assert listed_while_checked_out.json()[0]["checkout_status"] == "CHECKED_OUT_BY_ME"
    released = client.delete(
        f"/api/v1/attachments/{attachment_id}/checkout", headers=AUTH
    )
    assert released.status_code == 204
    second_content = b"second attachment in the same edit session"
    second_stage = client.post(
        f"/api/v1/items/{item['id']}/additional-files/staging",
        headers=AUTH,
        json={
            **manifest,
            "client_staging_id": str(uuid.uuid4()),
            "name": "Second attachment",
            "filename": "second.bin",
            "byte_size": len(second_content),
            "sha256": hashlib.sha256(second_content).hexdigest(),
        },
    )
    assert second_stage.status_code == 201, second_stage.text
    cancelled_second = client.delete(
        f"/api/v1/additional-files/staging/{second_stage.json()['id']}", headers=AUTH
    )
    assert cancelled_second.status_code == 204
    repeated = client.post(
        f"/api/v1/additional-files/staging/{stage['id']}/commit",
        headers=AUTH,
        json={"commit_id": commit_id},
    )
    assert repeated.status_code == 200

    history = client.get(
        f"/api/v1/additional-files/{attachment_id}/versions", headers=AUTH
    )
    assert history.status_code == 200
    assert [row["version_number"] for row in history.json()] == [1]

    replacement_content = b"reviewed replacement"
    replacement = client.post(
        f"/api/v1/items/{item['id']}/additional-files/staging",
        headers=AUTH,
        json={
            **manifest,
            "client_staging_id": str(uuid.uuid4()),
            "attachment_id": attachment_id,
            "byte_size": len(replacement_content),
            "sha256": hashlib.sha256(replacement_content).hexdigest(),
        },
    )
    assert replacement.status_code == 201, replacement.text
    replacement_stage = replacement.json()
    assert replacement_stage["action"] == "NEW_VERSION"
    uploaded_replacement = client.put(
        replacement_stage["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=replacement_content,
    )
    assert uploaded_replacement.status_code == 200
    committed_replacement = client.post(
        f"/api/v1/additional-files/staging/{replacement_stage['id']}/commit",
        headers=AUTH,
        json={"commit_id": str(uuid.uuid4())},
    )
    assert committed_replacement.status_code == 200, committed_replacement.text
    assert committed_replacement.json()["result"]["version_number"] == 2

    stale_content = b"staged before concurrent change"
    stale = client.post(
        f"/api/v1/items/{item['id']}/additional-files/staging",
        headers=AUTH,
        json={
            **manifest,
            "client_staging_id": str(uuid.uuid4()),
            "attachment_id": attachment_id,
            "byte_size": len(stale_content),
            "sha256": hashlib.sha256(stale_content).hexdigest(),
        },
    )
    assert stale.status_code == 201
    stale_stage = stale.json()
    assert client.put(
        stale_stage["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=stale_content,
    ).status_code == 200

    concurrent_content = b"concurrent direct replacement"
    concurrent = client.post(
        f"/api/v1/attachments/{attachment_id}/versions",
        headers=AUTH,
        json={
            "filename": "notes.bin",
            "mime_type": "application/octet-stream",
            "byte_size": len(concurrent_content),
            "sha256": hashlib.sha256(concurrent_content).hexdigest(),
        },
    )
    assert concurrent.status_code == 201
    concurrent_version = concurrent.json()["versions"][-1]
    assert client.put(
        concurrent_version["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=concurrent_content,
    ).status_code == 200
    stale_commit = client.post(
        f"/api/v1/additional-files/staging/{stale_stage['id']}/commit",
        headers=AUTH,
        json={"commit_id": str(uuid.uuid4())},
    )
    assert stale_commit.status_code == 409
    assert "changed after review" in stale_commit.json()["detail"]

    archived = client.post(f"/api/v1/attachments/{attachment_id}/archive", headers=AUTH)
    assert archived.status_code == 200
    assert archived.json()["lifecycle_state"] == "ARCHIVED"
    active_list = client.get(f"/api/v1/items/{item['id']}/attachments", headers=AUTH)
    assert all(row["id"] != attachment_id for row in active_list.json())
    archived_list = client.get(
        f"/api/v1/items/{item['id']}/attachments?include_archived=true", headers=AUTH
    )
    assert any(row["id"] == attachment_id for row in archived_list.json())
    restored = client.post(f"/api/v1/attachments/{attachment_id}/restore", headers=AUTH)
    assert restored.status_code == 200
    assert restored.json()["lifecycle_state"] == "ACTIVE"


def test_revision_edit_links_replaces_and_unlinks_item_additional_file_atomically() -> None:
    item = ready_item()
    original = b"item-level quality report"
    created = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        headers=AUTH,
        json={
            "name": "Quality report",
            "attachment_type": "OTHER",
            "filename": "quality.txt",
            "mime_type": "text/plain",
            "byte_size": len(original),
            "sha256": hashlib.sha256(original).hexdigest(),
        },
    )
    assert created.status_code == 201, created.text
    attachment = created.json()
    first_version = attachment["versions"][0]
    assert client.put(
        first_version["upload_url"],
        headers={**AUTH, "Content-Type": "text/plain"},
        content=original,
    ).status_code == 200

    def open_edit() -> dict:
        response = client.post(
            f"/api/v1/staging/items/{item['id']}/revisions/{item['current_revision_id']}/edit",
            headers=AUTH,
            json={"client_staging_id": str(uuid.uuid4())},
        )
        assert response.status_code == 201, response.text
        return response.json()

    def commit_edit(stage: dict) -> dict:
        validated = client.post(f"/api/v1/staging/{stage['id']}/validate", headers=AUTH)
        assert validated.status_code == 200, validated.text
        ready = client.post(
            f"/api/v1/staging/{stage['id']}/ready",
            headers=AUTH,
            json={"expected_snapshot_version": stage["snapshot_version"]},
        )
        assert ready.status_code == 200, ready.text
        committed = client.post(
            f"/api/v1/uploads/{stage['id']}/commit",
            headers=AUTH,
            json={
                "snapshot_version": stage["snapshot_version"],
                "commit_id": str(uuid.uuid4()),
            },
        )
        assert committed.status_code == 200, committed.text
        return committed.json()

    stage = open_edit()
    proposal = stage["proposal"]
    proposal["artifacts"].append({
        "client_artifact_id": str(uuid.uuid4()),
        "existing_artifact_id": first_version["id"],
        "attachment_id": attachment["id"],
        "owner_staged_file_id": None,
        "artifact_type": "DOCUMENT",
        "name": "Quality report",
        "description": None,
        "classification": "AUTHORITATIVE",
        "relation_type": "ATTACHMENT",
        "sequence": len(proposal["artifacts"]),
        "is_primary": False,
        "files": [],
    })
    linked = client.put(
        f"/api/v1/staging/{stage['id']}/proposal",
        headers=AUTH,
        json={**proposal, "expected_snapshot_version": stage["snapshot_version"]},
    )
    assert linked.status_code == 200, linked.text
    linked_stage = linked.json()
    linked_result = commit_edit(linked_stage)
    pinned = client.get(
        f"/api/v1/items/{item['id']}/revisions/{item['current_revision_id']}/attachments",
        headers=AUTH,
    )
    assert pinned.status_code == 200
    assert pinned.json()[0]["latest_ready_version"]["id"] == first_version["id"]

    replacement = b"revision-reviewed quality report"
    stage = open_edit()
    logical_path = f"attachments/{uuid.uuid4()}-quality.txt"
    added = client.post(
        f"/api/v1/staging/{stage['id']}/files",
        headers=AUTH,
        json={
            "expected_snapshot_version": stage["snapshot_version"],
            "logical_path": logical_path,
            "filename": "quality.txt",
            "mime_type": "text/plain",
            "byte_size": len(replacement),
            "sha256": hashlib.sha256(replacement).hexdigest(),
        },
    )
    assert added.status_code == 201, added.text
    staged = added.json()
    staged_file = next(row for row in staged["files"] if row["logical_path"] == logical_path)
    uploaded = client.put(
        staged_file["upload_url"],
        headers={**AUTH, "Content-Type": "text/plain"},
        content=replacement,
    )
    assert uploaded.status_code == 200, uploaded.text
    staged = uploaded.json()
    retained_cad = [
        row for row in staged["proposal"]["artifacts"]
        if row.get("relation_type") != "ATTACHMENT"
    ]
    retained_cad.append({
        "client_artifact_id": str(uuid.uuid4()),
        "attachment_id": attachment["id"],
        "owner_staged_file_id": None,
        "artifact_type": "DOCUMENT",
        "name": "Quality report",
        "description": None,
        "classification": "AUTHORITATIVE",
        "relation_type": "ATTACHMENT",
        "sequence": len(retained_cad),
        "is_primary": False,
        "files": [{
            "staged_file_id": staged_file["id"],
            "reference_name": "PRIMARY",
            "role": "PRIMARY",
        }],
    })
    updated = client.put(
        f"/api/v1/staging/{stage['id']}/proposal",
        headers=AUTH,
        json={
            **staged["proposal"],
            "artifacts": retained_cad,
            "expected_snapshot_version": staged["snapshot_version"],
        },
    )
    assert updated.status_code == 200, updated.text
    replaced_result = commit_edit(updated.json())
    versions = client.get(
        f"/api/v1/additional-files/{attachment['id']}/versions", headers=AUTH
    )
    assert versions.status_code == 200
    assert [row["version_number"] for row in versions.json()] == [1, 2]
    second_version = versions.json()[-1]
    assert second_version["sha256"] == hashlib.sha256(replacement).hexdigest()

    stage = open_edit()
    proposal = stage["proposal"]
    proposal["artifacts"] = [
        row for row in proposal["artifacts"] if row.get("relation_type") != "ATTACHMENT"
    ]
    unlinked = client.put(
        f"/api/v1/staging/{stage['id']}/proposal",
        headers=AUTH,
        json={**proposal, "expected_snapshot_version": stage["snapshot_version"]},
    )
    assert unlinked.status_code == 200, unlinked.text
    commit_edit(unlinked.json())
    current = client.get(
        f"/api/v1/items/{item['id']}/revisions/{item['current_revision_id']}/attachments",
        headers=AUTH,
    )
    assert current.status_code == 200
    assert current.json() == []

    with SessionLocal() as db:
        old_snapshot_ids = {
            uuid.UUID(linked_result["content_snapshot_id"]),
            uuid.UUID(replaced_result["content_snapshot_id"]),
        }
        pinned_artifact_ids = set(db.scalars(
            select(RevisionArtifactRelation.artifact_id)
            .join(
                RevisionContentSnapshotArtifact,
                RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
            )
            .where(RevisionContentSnapshotArtifact.snapshot_id.in_(old_snapshot_ids))
        ))
        assert uuid.UUID(first_version["id"]) in pinned_artifact_ids
        assert uuid.UUID(second_version["id"]) in pinned_artifact_ids


def test_canonical_item_crud_search_revision_and_checkout_contracts() -> None:
    item_number = f"CANON-{uuid.uuid4().hex}"
    created = client.post(
        "/api/v1/items",
        headers=AUTH,
        json={"item_number": item_number, "name": "Canonical Item", "item_type": "PART"},
    )
    assert created.status_code == 201
    item = created.json()
    assert item["item_number"] == item_number
    assert "part_number" not in item

    updated = client.patch(
        f"/api/v1/items/{item['id']}",
        headers=AUTH,
        json={
            "name": "Updated Canonical Item",
            "description": "Updated",
            "category": "Test fixtures",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated Canonical Item"
    assert updated.json()["description"] == "Updated"
    assert updated.json()["category"] == "Test fixtures"

    searched = client.post(
        "/api/v1/items/search",
        headers=AUTH,
        json={"query": item_number, "item_type": "PART", "include_incomplete": True},
    )
    assert searched.status_code == 200
    match = next(row for row in searched.json()["items"] if row["item"]["id"] == item["id"])
    assert match["item"]["item_number"] == item_number
    assert "part" not in match

    revision = client.post(
        f"/api/v1/items/{item['id']}/revisions",
        headers=AUTH,
        json={
            "revision_code": "A",
            "cad_system": "FreeCAD",
            "native_format": "FCStd",
            "metadata": {"schema_version": 1, "properties": {}},
        },
    )
    assert revision.status_code == 201
    assert revision.json()["item_id"] == item["id"]
    assert "part_id" not in revision.json()
    history = client.get(f"/api/v1/items/{item['id']}/revisions", headers=AUTH)
    assert history.status_code == 200
    assert history.json()[0]["item_id"] == item["id"]

    item_history = client.get(f"/api/v1/items/{item['id']}/history", headers=AUTH)
    assert item_history.status_code == 200
    assert [entry["action"] for entry in item_history.json()][:3] == [
        "revision.created",
        "item.updated",
        "item.created",
    ]
    assert all(entry["changed_by"] == "Local Engineer" for entry in item_history.json())
    update_entry = next(entry for entry in item_history.json() if entry["action"] == "item.updated")
    assert update_entry["details"]["changes"] == {
        "category": {"before": None, "after": "Test fixtures"},
        "description": {"before": None, "after": "Updated"},
        "name": {"before": "Canonical Item", "after": "Updated Canonical Item"},
    }

    revision_history = client.get(
        f"/api/v1/items/{item['id']}/revisions/{revision.json()['id']}/history",
        headers=AUTH,
    )
    assert revision_history.status_code == 200
    assert revision_history.json()[0]["action"] == "revision.created"
    assert revision_history.json()[0]["changed_by"] == "Local Engineer"

    checkout = client.post(f"/api/v1/items/{item['id']}/checkout", headers=AUTH)
    assert checkout.status_code == 201
    assert checkout.json()["item_id"] == item["id"]
    assert "part_id" not in checkout.json()


def test_administrator_can_soft_delete_and_restore_item() -> None:
    item_number = f"DELETE-{uuid.uuid4().hex}"
    created = client.post(
        "/api/v1/items",
        headers=AUTH,
        json={"item_number": item_number, "name": "Incorrect Item", "item_type": "PART"},
    )
    assert created.status_code == 201
    item = created.json()

    deleted = client.delete(f"/api/v1/items/{item['id']}", headers=AUTH)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/items/{item['id']}", headers=AUTH).status_code == 404

    archived = client.get(
        "/api/v1/items?include_incomplete=true&include_archived=true&limit=100",
        headers=AUTH,
    )
    deleted_item = next(row for row in archived.json()["items"] if row["id"] == item["id"])
    assert deleted_item["status"] == "ARCHIVED"

    restored = client.patch(f"/api/v1/items/{item['id']}", headers=AUTH, json={"status": "ACTIVE"})
    assert restored.status_code == 200
    assert restored.json()["status"] == "ACTIVE"


def test_attachment_validation_integrity_and_archived_item_rules() -> None:
    item = ready_item()
    unrestricted_type = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        headers=AUTH,
        json={
            "name": "Bad drawing",
            "attachment_type": "DRAWING",
            "filename": "drawing.exe",
            "mime_type": "application/octet-stream",
            "byte_size": 4,
            "sha256": hashlib.sha256(b"test").hexdigest(),
        },
    )
    assert unrestricted_type.status_code == 201

    drawing = b"%PDF-integrity"
    created = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        headers=AUTH,
        json={
            "name": "Integrity drawing",
            "attachment_type": "DRAWING",
            "filename": "integrity.pdf",
            "mime_type": "application/pdf",
            "byte_size": len(drawing),
            "sha256": hashlib.sha256(drawing).hexdigest(),
        },
    ).json()
    version = created["versions"][0]
    wrong_content_type = client.put(
        version["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=drawing,
    )
    assert wrong_content_type.status_code == 415
    wrong_hash = client.put(
        version["upload_url"],
        headers={**AUTH, "Content-Type": "application/pdf"},
        content=b"x" * len(drawing),
    )
    assert wrong_hash.status_code == 422

    archived = client.patch(
        f"/api/v1/items/{item['id']}", headers=AUTH, json={"status": "ARCHIVED"}
    )
    assert archived.status_code == 200
    rejected = client.post(
        f"/api/v1/attachments/{created['id']}/versions",
        headers=AUTH,
        json={
            "filename": "integrity-b.pdf",
            "mime_type": "application/pdf",
            "byte_size": len(drawing),
            "sha256": hashlib.sha256(drawing).hexdigest(),
        },
    )
    assert rejected.status_code == 409


def test_copy_revision_preserves_source_artifact_and_updates_current_revision() -> None:
    item = ready_item()
    source = client.get(f"/api/v1/items/{item['id']}/revisions", headers=AUTH).json()[0]
    with SessionLocal() as db:
        source_row = db.get(Revision, uuid.UUID(source["id"]))
        source_row.state = "READY"
        db.commit()

    copied = client.post(
        f"/api/v1/items/{item['id']}/revisions/copy",
        headers=AUTH,
        json={"source_revision_id": source["id"], "revision_code": "B"},
    )
    assert copied.status_code == 201, copied.text
    revision = copied.json()
    assert revision["revision_code"] == "B"
    assert revision["state"] == "PROCESSING"
    assert revision["metadata_status"] == "UNAVAILABLE"
    assert "current_working_version_id" not in revision

    refreshed = client.get(f"/api/v1/items/{item['id']}", headers=AUTH).json()
    assert refreshed["current_revision_id"] == revision["id"]
    relations = client.get(
        f"/api/v1/item-revisions/{revision['id']}/artifacts", headers=AUTH
    ).json()
    source_relations = client.get(
        f"/api/v1/item-revisions/{source['id']}/artifacts", headers=AUTH
    ).json()
    copied_specification = next(
        relation for relation in relations if relation["relation_type"] == "SPECIFICATION"
    )
    source_specification = next(
        relation
        for relation in source_relations
        if relation["relation_type"] == "SPECIFICATION"
    )
    assert copied_specification["artifact"]["id"] != source_specification["artifact"]["id"]
    assert (
        copied_specification["artifact"]["artifact_key"]
        == source_specification["artifact"]["artifact_key"]
    )
    assert (
        copied_specification["artifact"]["generation"]
        == source_specification["artifact"]["generation"] + 1
    )
    history = client.get(
        f"/api/v1/artifacts/{copied_specification['artifact']['id']}/history",
        headers=AUTH,
    )
    assert history.status_code == 200, history.text
    generation_event = next(
        entry for entry in history.json() if entry["action"] == "artifact.generation_created"
    )
    assert generation_event["details"]["source_artifact_id"] == source_specification[
        "artifact"
    ]["id"]
    native = next(
        file
        for relation in relations
        if relation["artifact"]["artifact_type"] == "CAD_MODEL"
        for file in relation["artifact"]["files"]
        if file["role"] == "PRIMARY"
    )
    downloaded = client.get(native["download_url"], headers=AUTH)
    assert downloaded.status_code == 200
    assert downloaded.content == b"attachment-test-fcstd"


def test_admin_item_header_filters_page_across_archived_and_incomplete_items() -> None:
    prefix = f"FILTER-{uuid.uuid4().hex[:12]}"
    created = []
    for suffix, item_type in (("PART", "PART"), ("ASM", "ASSEMBLY"), ("ARCHIVED", "PART")):
        response = client.post(
            "/api/v1/items", headers=AUTH,
            json={"item_number": f"{prefix}-{suffix}", "name": f"Filter {suffix}",
                  "item_type": item_type},
        )
        assert response.status_code == 201
        created.append(response.json())
    assert client.delete(f"/api/v1/items/{created[2]['id']}", headers=AUTH).status_code == 204
    params = [
        ("include_archived", "true"), ("include_incomplete", "true"),
        ("include_facets", "true"), ("limit", "1"), ("item_number_regex", f"^{prefix}-"),
        ("item_types", "PART"), ("item_types", "ASSEMBLY"),
        ("item_statuses", "ACTIVE"), ("item_statuses", "ARCHIVED"), ("cad_systems", ""),
    ]
    response = client.get("/api/v1/items", headers=AUTH, params=params)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3 and len(body["items"]) == 1
    assert set(body["facets"]["item_types"]) == {"PART", "ASSEMBLY"}
    assert set(body["facets"]["item_statuses"]) == {"ACTIVE", "ARCHIVED"}
    assert body["facets"]["cad_systems"] == [None]
    second = client.get("/api/v1/items", headers=AUTH, params=[*params, ("offset", "1")])
    assert second.json()["total"] == 3
    assert second.json()["items"][0]["id"] != body["items"][0]["id"]
    empty = client.get(
        "/api/v1/items", headers=AUTH,
        params=[*(pair for pair in params if pair[0] != "item_types"),
                ("item_types", "__modelryx_no_values__")],
    )
    assert empty.status_code == 200 and empty.json()["total"] == 0
