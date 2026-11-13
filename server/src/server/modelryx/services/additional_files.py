import json
from dataclasses import asdict, dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modelryx.core.db import SessionLocal
from modelryx.models import (
    Artifact,
    ArtifactFile,
    Attachment,
    ContentBlob,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
)


@dataclass(frozen=True)
class AdditionalFileConsistencyFinding:
    code: str
    attachment_id: str
    version_id: str | None
    detail: str


@dataclass(frozen=True)
class AdditionalFileConsistencyReport:
    attachments: int
    versions: int
    ready_versions: int
    exact_duplicate_groups: int
    findings: list[AdditionalFileConsistencyFinding]


def inspect_additional_files(db: Session) -> AdditionalFileConsistencyReport:
    """Read-only audit of relation-based attachment consistency."""
    attachments = list(db.scalars(select(Attachment).order_by(Attachment.id)))
    versions = list(db.scalars(
        select(Artifact)
        .where(Artifact.attachment_id.is_not(None))
        .order_by(Artifact.attachment_id, Artifact.generation, Artifact.id)
    ))
    findings: list[AdditionalFileConsistencyFinding] = []

    versions_by_attachment: dict[object, list[Artifact]] = {}
    for version in versions:
        versions_by_attachment.setdefault(version.attachment_id, []).append(version)
    for attachment in attachments:
        if not versions_by_attachment.get(attachment.id):
            findings.append(AdditionalFileConsistencyFinding(
                code="ATTACHMENT_WITHOUT_VERSION",
                attachment_id=str(attachment.id),
                version_id=None,
                detail="The logical Additional File has no immutable file version.",
            ))

    for version in versions:
        files = list(db.scalars(
            select(ArtifactFile).where(ArtifactFile.artifact_id == version.id)
        ))
        verified = [file for file in files if file.state == "VERIFIED"]
        if version.state == "READY" and len(verified) != 1:
            findings.append(AdditionalFileConsistencyFinding(
                code="READY_VERSION_CONTENT_COUNT",
                attachment_id=str(version.attachment_id),
                version_id=str(version.id),
                detail=f"READY version has {len(verified)} verified content files; expected one.",
            ))
        for file in verified:
            blob = db.get(ContentBlob, file.content_blob_id) if file.content_blob_id else None
            if blob is None:
                findings.append(AdditionalFileConsistencyFinding(
                    code="VERIFIED_CONTENT_BLOB_MISSING",
                    attachment_id=str(version.attachment_id),
                    version_id=str(version.id),
                    detail="Verified Additional File content has no content-addressed blob.",
                ))
            elif (
                blob.state != "VERIFIED"
                or blob.sha256 != file.sha256
                or blob.byte_size != file.byte_size
            ):
                findings.append(AdditionalFileConsistencyFinding(
                    code="CONTENT_BLOB_MISMATCH",
                    attachment_id=str(version.attachment_id),
                    version_id=str(version.id),
                    detail="Content blob state, SHA-256, or size does not match its file record.",
                ))
            revision = db.get(Revision, file.revision_id) if file.revision_id else None
            if revision is None:
                continue
            attachment = db.get(Attachment, version.attachment_id)
            if attachment is not None and revision.part_id != attachment.item_id:
                findings.append(AdditionalFileConsistencyFinding(
                    code="CROSS_ITEM_LEGACY_REVISION",
                    attachment_id=str(version.attachment_id),
                    version_id=str(version.id),
                    detail="Legacy ArtifactFile revision points to a different Item.",
                ))
            relation = db.scalar(select(RevisionArtifactRelation.id).where(
                RevisionArtifactRelation.revision_id == revision.id,
                RevisionArtifactRelation.artifact_id == version.id,
            ))
            if relation is None:
                findings.append(AdditionalFileConsistencyFinding(
                    code="LEGACY_REVISION_RELATION_MISSING",
                    attachment_id=str(version.attachment_id),
                    version_id=str(version.id),
                    detail=(
                        "ArtifactFile carries a legacy revision ID but the immutable Artifact "
                        "version has no RevisionArtifactRelation."
                    ),
                ))
            else:
                latest_snapshot_id = db.scalar(
                    select(RevisionContentSnapshot.id)
                    .where(RevisionContentSnapshot.revision_id == revision.id)
                    .order_by(RevisionContentSnapshot.sequence.desc())
                    .limit(1)
                )
                snapshot_link = None if latest_snapshot_id is None else db.scalar(
                    select(RevisionContentSnapshotArtifact.relation_id).where(
                        RevisionContentSnapshotArtifact.snapshot_id == latest_snapshot_id,
                        RevisionContentSnapshotArtifact.relation_id == relation,
                    )
                )
                if latest_snapshot_id is None or snapshot_link is None:
                    findings.append(AdditionalFileConsistencyFinding(
                        code="LATEST_SNAPSHOT_RELATION_MISSING",
                        attachment_id=str(version.attachment_id),
                        version_id=str(version.id),
                        detail="Revision relation is absent from the latest content snapshot.",
                    ))

    duplicate_groups = db.scalar(
        select(func.count()).select_from(
            select(ArtifactFile.sha256, ArtifactFile.byte_size)
            .join(Artifact, Artifact.id == ArtifactFile.artifact_id)
            .where(
                Artifact.attachment_id.is_not(None),
                Artifact.state == "READY",
                ArtifactFile.state == "VERIFIED",
            )
            .group_by(ArtifactFile.sha256, ArtifactFile.byte_size)
            .having(func.count(ArtifactFile.id) > 1)
            .subquery()
        )
    ) or 0
    return AdditionalFileConsistencyReport(
        attachments=len(attachments),
        versions=len(versions),
        ready_versions=sum(version.state == "READY" for version in versions),
        exact_duplicate_groups=duplicate_groups,
        findings=findings,
    )


def main() -> None:
    with SessionLocal() as db:
        report = inspect_additional_files(db)
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()
