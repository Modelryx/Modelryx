import argparse
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    AdditionalFileStage,
    Artifact,
    ArtifactFile,
    ContentBlob,
    StagingFile,
)
from modelryx.services.processing import recover_abandoned_jobs
from modelryx.services.storage import LocalStorageProvider


@dataclass
class ReconciliationResult:
    orphaned: list[Path]
    missing: list[str]
    removed: int
    expired_attachment_uploads: int
    expired_additional_file_stages: int
    unreferenced_blobs: int
    recovered_jobs: int


def reconcile(*, delete: bool = False) -> ReconciliationResult:
    settings = get_settings()
    storage = LocalStorageProvider(settings.storage_root)
    with SessionLocal() as db:
        expired_attachments = list(
            db.scalars(
                select(Artifact).where(
                    Artifact.attachment_id.is_not(None),
                    Artifact.state == "UPLOADING",
                    Artifact.expires_at.is_not(None),
                    Artifact.expires_at < datetime.now(UTC),
                )
            )
        )
        for version in expired_attachments:
            version.state = "EXPIRED"
            artifact = db.scalar(select(ArtifactFile).where(ArtifactFile.artifact_id == version.id))
            artifact.state = "CANCELLED"
            if delete:
                storage.path_for(artifact.storage_key).unlink(missing_ok=True)
                storage.partial_path(artifact.storage_key).unlink(missing_ok=True)
        stage_cutoff = datetime.now(UTC).timestamp() - settings.upload_expiry_seconds
        expired_stages = list(db.scalars(select(AdditionalFileStage).where(
            AdditionalFileStage.status.in_(["DRAFT", "READY"]),
            AdditionalFileStage.updated_at < datetime.fromtimestamp(stage_cutoff, UTC),
        )))
        for stage in expired_stages:
            stage.status = "CANCELLED"
            if delete:
                storage.path_for(stage.storage_key).unlink(missing_ok=True)
                storage.partial_path(stage.storage_key).unlink(missing_ok=True)
                stage.content_blob_id = None
        recovery = recover_abandoned_jobs(db, settings)
        db.flush()
        artifacts = list(db.scalars(select(ArtifactFile)))
        blobs = list(db.scalars(select(ContentBlob)))
        additional_file_stages = list(db.scalars(select(AdditionalFileStage)))
        staged_files = list(db.scalars(select(StagingFile)))
        referenced_blob_ids = {
            row.content_blob_id for row in artifacts if row.content_blob_id is not None
        } | {
            row.content_blob_id for row in additional_file_stages
            if row.content_blob_id is not None and row.status != "CANCELLED"
        }
        unreferenced_blobs = [blob for blob in blobs if blob.id not in referenced_blob_ids]
        if delete:
            for blob in unreferenced_blobs:
                storage.path_for(blob.storage_key).unlink(missing_ok=True)
                db.delete(blob)
            blobs = [blob for blob in blobs if blob.id in referenced_blob_ids]
        db.commit()
    known = {
        storage.path_for(row.storage_key)
        for row in [
            *(artifact for artifact in artifacts if artifact.content_blob_id is None),
            *blobs,
            *staged_files,
        ]
    }
    known.update(
        storage.partial_path(stage.storage_key)
        for stage in additional_file_stages
        if stage.status == "DRAFT"
    )
    missing = [
        artifact.storage_key
        for artifact in artifacts
        if artifact.state == "VERIFIED"
        and artifact.content_blob_id is None
        and not storage.exists(artifact.storage_key)
    ]
    missing.extend(
        blob.storage_key
        for blob in blobs
        if blob.state == "VERIFIED" and not storage.exists(blob.storage_key)
    )
    cutoff = time.time() - settings.orphan_grace_seconds
    orphaned = [
        path
        for path in settings.storage_root.rglob("*")
        if path.is_file() and path not in known and path.stat().st_mtime < cutoff
    ]
    removed = 0
    if delete:
        for path in orphaned:
            path.unlink()
            removed += 1
    return ReconciliationResult(
        orphaned=orphaned,
        missing=missing,
        removed=removed,
        expired_attachment_uploads=len(expired_attachments),
        expired_additional_file_stages=len(expired_stages),
        unreferenced_blobs=len(unreferenced_blobs),
        recovered_jobs=recovery.requeued + recovery.failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile database artifacts and local storage")
    parser.add_argument("--delete", action="store_true", help="delete aged orphan files")
    args = parser.parse_args()
    result = reconcile(delete=args.delete)
    print(
        f"orphaned={len(result.orphaned)} missing={len(result.missing)} "
        f"removed={result.removed}"
        f" expired_attachment_uploads={result.expired_attachment_uploads}"
        f" expired_additional_file_stages={result.expired_additional_file_stages}"
        f" unreferenced_blobs={result.unreferenced_blobs}"
        f" recovered_jobs={result.recovered_jobs}"
    )
    for storage_key in result.missing:
        print(f"missing: {storage_key}")
    for path in result.orphaned:
        print(f"orphaned: {path}")


if __name__ == "__main__":
    main()
