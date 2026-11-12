import hashlib
import json
import uuid
import zipfile
from pathlib import Path

from artifact_helpers import commit_item
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.bundles import cad_source_signature
from modelryx.api.main import app
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    ArtifactFile,
    DownloadBundle,
    ProcessingJob,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
)
from modelryx.services.processing import process_job
from sqlalchemy import select

client = TestClient(app)


def test_deterministic_assembly_bundle_contains_hash_manifest() -> None:
    suffix = uuid.uuid4().hex
    main = f"main-{suffix}".encode()
    child = f"child-{suffix}".encode()
    completed = commit_item(
        client,
        item_number=f"BUNDLE-{suffix}",
        name="Bundle assembly",
        item_type="ASSEMBLY",
        files=[("assembly.FCStd", main), ("parts/child.FCStd", child)],
        bom=[("assembly.FCStd", "parts/child.FCStd", "child-1")],
    )
    revision_id = completed["revision_id"]
    # Model a folder upload, where each authoritative native file retains the
    # package-relative path used by the CAD references.
    with SessionLocal() as db:
        files = list(
            db.scalars(
                select(ArtifactFile).where(
                    ArtifactFile.sha256.in_(
                        [hashlib.sha256(main).hexdigest(), hashlib.sha256(child).hexdigest()]
                    )
                )
            )
        )
        for file in files:
            file.reference_name = (
                "assembly.FCStd"
                if file.original_filename == "assembly.FCStd"
                else "parts/child.FCStd"
            )
        db.commit()
    requested = client.post(f"/api/v1/revisions/{revision_id}/download-bundles", headers=AUTH)
    assert requested.status_code == 202, requested.text
    with SessionLocal() as db:
        bundle = db.get(DownloadBundle, uuid.UUID(requested.json()["id"]))
        job = db.get(ProcessingJob, bundle.processing_job_id)
        job.state = "RUNNING"
        job.attempt_count = 1
        db.commit()
        process_job(db, job, get_settings())
        db.refresh(bundle)
        assert bundle.source_signature == cad_source_signature(db, uuid.UUID(revision_id))
    ready = client.get(f"/api/v1/download-bundles/{requested.json()['id']}", headers=AUTH)
    assert ready.json()["state"] == "READY"
    workspace = client.get("/api/v1/me/workspace", headers=AUTH)
    assert workspace.status_code == 200
    task = next(
        row for row in workspace.json()["uploads"]
        if row["id"] == requested.json()["id"]
    )
    assert task["operation"] == "DOWNLOAD_CAD_FILES"
    assert task["is_finished"] is True
    assert task["can_resume"] is True
    assert task["download_url"].endswith(f"/{requested.json()['id']}/download")
    reused = client.post(f"/api/v1/revisions/{revision_id}/download-bundles", headers=AUTH)
    assert reused.status_code == 202
    assert reused.json()["id"] == requested.json()["id"]
    downloaded = client.get(
        f"/api/v1/download-bundles/{requested.json()['id']}/download", headers=AUTH
    )
    assert downloaded.status_code == 200
    assert "accept-ranges" in downloaded.headers
    archive_path = Path(get_settings().storage_root) / f"bundle-test-{suffix}.zip"
    archive_path.write_bytes(downloaded.content)
    try:
        with zipfile.ZipFile(archive_path) as package:
            manifest = json.loads(package.read("manifest.json"))
            assert manifest["schemaVersion"] == 2
            assert manifest["root"]["path"] == "assembly.FCStd"
            assert manifest["root"]["revisionId"] == revision_id
            assert len(manifest["files"]) == 2
            assert {item["sha256"] for item in manifest["files"]} == {
                hashlib.sha256(main).hexdigest(),
                hashlib.sha256(child).hexdigest(),
            }
            assert all(".." not in item["path"] for item in manifest["files"])
            assert {item["path"] for item in manifest["files"]} == {
                "assembly.FCStd",
                "parts/child.FCStd",
            }
    finally:
        archive_path.unlink(missing_ok=True)

    archived = client.patch(
        f"/api/v1/download-bundles/{requested.json()['id']}/workspace",
        headers=AUTH,
        json={"action": "ARCHIVE"},
    )
    assert archived.status_code == 204
    task = next(
        row for row in client.get("/api/v1/me/workspace", headers=AUTH).json()["uploads"]
        if row["id"] == requested.json()["id"]
    )
    assert task["is_archived"] is True
    removed = client.patch(
        f"/api/v1/download-bundles/{requested.json()['id']}/workspace",
        headers=AUTH,
        json={"action": "REMOVE"},
    )
    assert removed.status_code == 204
    assert all(
        row["id"] != requested.json()["id"]
        for row in client.get("/api/v1/me/workspace", headers=AUTH).json()["uploads"]
    )


def test_part_bundle_keeps_primary_file_at_archive_root() -> None:
    suffix = uuid.uuid4().hex
    content = f"part-{suffix}".encode()
    completed = commit_item(
        client,
        item_number=f"BUNDLE-PART-{suffix}",
        name="Bundle part",
        item_type="PART",
        files=[("nested/source-part.ipt", content)],
    )
    revision_id = completed["revision_id"]
    with SessionLocal() as db:
        artifact = db.scalar(select(ArtifactFile).where(
            ArtifactFile.sha256 == hashlib.sha256(content).hexdigest()
        ))
        assert artifact is not None
        artifact.reference_name = None
        db.commit()

    requested = client.post(f"/api/v1/revisions/{revision_id}/download-bundles", headers=AUTH)
    assert requested.status_code == 202, requested.text
    with SessionLocal() as db:
        bundle = db.get(DownloadBundle, uuid.UUID(requested.json()["id"]))
        job = db.get(ProcessingJob, bundle.processing_job_id)
        job.state = "RUNNING"
        job.attempt_count = 1
        db.commit()
        process_job(db, job, get_settings())

    downloaded = client.get(
        f"/api/v1/download-bundles/{requested.json()['id']}/download", headers=AUTH
    )
    assert downloaded.status_code == 200
    archive_path = Path(get_settings().storage_root) / f"part-bundle-test-{suffix}.zip"
    archive_path.write_bytes(downloaded.content)
    try:
        with zipfile.ZipFile(archive_path) as package:
            assert "source-part.ipt" in package.namelist()
            assert "main/source-part.ipt" not in package.namelist()
    finally:
        archive_path.unlink(missing_ok=True)


def test_failed_download_bundle_can_be_retried() -> None:
    suffix = uuid.uuid4().hex
    completed = commit_item(
        client,
        item_number=f"BUNDLE-RETRY-{suffix}",
        name="Retry bundle",
        item_type="PART",
        files=[("retry.ipt", suffix.encode())],
    )
    requested = client.post(
        f"/api/v1/revisions/{completed['revision_id']}/download-bundles", headers=AUTH
    )
    bundle_id = uuid.UUID(requested.json()["id"])
    with SessionLocal() as db:
        bundle = db.get(DownloadBundle, bundle_id)
        old_job_id = bundle.processing_job_id
        bundle.state = "FAILED"
        bundle.error_detail = "worker stopped"
        job = db.get(ProcessingJob, old_job_id)
        job.state = "FAILED"
        job.error_detail = "worker stopped"
        db.commit()

    retried = client.post(f"/api/v1/download-bundles/{bundle_id}/retry", headers=AUTH)
    assert retried.status_code == 202, retried.text
    assert retried.json()["state"] == "PENDING"
    assert retried.json()["processing_job_id"] != str(old_job_id)


def test_download_bundle_uses_selected_revision_version(tmp_path: Path) -> None:
    suffix = uuid.uuid4().hex
    old_bytes, new_bytes = f"old-{suffix}".encode(), f"new-{suffix}".encode()
    original = commit_item(
        client, item_number=f"HISTORY-{suffix}", name="History source", item_type="PART",
        files=[("old.FCStd", old_bytes)],
    )
    other = commit_item(
        client, item_number=f"OTHER-{suffix}", name="Other source", item_type="PART",
        files=[("new.FCStd", new_bytes)],
    )
    revision_id = uuid.UUID(original["revision_id"])
    with SessionLocal() as db:
        old_snapshot = db.scalar(select(RevisionContentSnapshot).where(
            RevisionContentSnapshot.revision_id == revision_id
        ))
        source = db.scalar(select(ArtifactFile).where(
            ArtifactFile.sha256 == hashlib.sha256(new_bytes).hexdigest()
        ))
        assert old_snapshot is not None and source is not None
        old_snapshot_id = old_snapshot.id
        relation = RevisionArtifactRelation(
            revision_id=revision_id, artifact_id=source.artifact_id,
            relation_type="SPECIFICATION", is_primary=True, created_by=old_snapshot.created_by,
        )
        db.add(relation)
        db.flush()
        new_snapshot = RevisionContentSnapshot(
            revision_id=revision_id, sequence=old_snapshot.sequence + 1,
            commit_id=uuid.uuid4(), created_by=old_snapshot.created_by,
        )
        db.add(new_snapshot)
        db.flush()
        db.add(RevisionContentSnapshotArtifact(
            snapshot_id=new_snapshot.id, relation_id=relation.id
        ))
        new_snapshot_id = new_snapshot.id
        db.commit()

    url = f"/api/v1/revisions/{revision_id}/download-bundles"
    old = client.post(url, headers=AUTH, params={"snapshot_id": str(old_snapshot_id)})
    current = client.post(url, headers=AUTH)
    assert old.status_code == current.status_code == 202
    assert old.json()["snapshot_id"] == str(old_snapshot_id)
    assert current.json()["snapshot_id"] == str(new_snapshot_id)
    assert old.json()["id"] != current.json()["id"]
    repeat = client.post(url, headers=AUTH, params={"snapshot_id": str(old_snapshot_id)})
    assert repeat.json()["id"] == old.json()["id"]
    foreign = client.post(url, headers=AUTH, params={"snapshot_id": other["content_snapshot_id"]})
    assert foreign.status_code == 404

    for response, expected in ((old, old_bytes), (current, new_bytes)):
        with SessionLocal() as db:
            bundle = db.get(DownloadBundle, uuid.UUID(response.json()["id"]))
            job = db.get(ProcessingJob, bundle.processing_job_id)
            job.state = "RUNNING"
            job.attempt_count = 1
            db.commit()
            process_job(db, job, get_settings())
        downloaded = client.get(
            f"/api/v1/download-bundles/{response.json()['id']}/download", headers=AUTH
        )
        assert downloaded.status_code == 200
        archive = tmp_path / f"{response.json()['id']}.zip"
        archive.write_bytes(downloaded.content)
        with zipfile.ZipFile(archive) as package:
            manifest = json.loads(package.read("manifest.json"))
            assert manifest["snapshotId"] == response.json()["snapshot_id"]
            assert package.read(manifest["root"]["path"]) == expected
