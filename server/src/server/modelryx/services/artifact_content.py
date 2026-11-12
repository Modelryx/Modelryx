import uuid
from collections.abc import Collection

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modelryx.models import (
    Artifact,
    ArtifactFile,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
)


def latest_content_snapshot_id(db: Session, revision_id: uuid.UUID) -> uuid.UUID | None:
    return db.scalar(
        select(RevisionContentSnapshot.id)
        .where(RevisionContentSnapshot.revision_id == revision_id)
        .order_by(RevisionContentSnapshot.sequence.desc())
    )


def current_artifact_files(
    db: Session,
    revision_id: uuid.UUID,
    *,
    artifact_types: Collection[str] | None = None,
    states: Collection[str] = ("VERIFIED",),
) -> list[ArtifactFile]:
    """Return files related through the latest immutable revision content snapshot."""
    snapshot_id = latest_content_snapshot_id(db, revision_id)
    if snapshot_id is None:
        return []
    return artifact_files_for_snapshot(
        db, revision_id, snapshot_id, artifact_types=artifact_types, states=states
    )


def artifact_files_for_snapshot(
    db: Session,
    revision_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    *,
    artifact_types: Collection[str] | None = None,
    states: Collection[str] = ("VERIFIED",),
) -> list[ArtifactFile]:
    """Return files included in one immutable revision content snapshot."""
    statement = (
        select(ArtifactFile)
        .join(Artifact, Artifact.id == ArtifactFile.artifact_id)
        .join(RevisionArtifactRelation, RevisionArtifactRelation.artifact_id == Artifact.id)
        .join(
            RevisionContentSnapshotArtifact,
            RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
        )
        .where(
            RevisionArtifactRelation.revision_id == revision_id,
            RevisionContentSnapshotArtifact.snapshot_id == snapshot_id,
            ArtifactFile.state.in_(states),
        )
        .order_by(
            RevisionContentSnapshotArtifact.sequence,
            ArtifactFile.reference_name,
            ArtifactFile.id,
        )
    )
    if artifact_types is not None:
        statement = statement.where(Artifact.artifact_type.in_(artifact_types))
    return list(db.scalars(statement).unique())


def revisions_with_current_artifact_types(
    db: Session,
    revision_ids: Collection[uuid.UUID],
    artifact_types: Collection[str],
    *,
    states: Collection[str] = ("VERIFIED",),
) -> set[uuid.UUID]:
    """Return revisions whose latest snapshot contains a matching Artifact file."""
    if not revision_ids or not artifact_types:
        return set()
    latest_sequences = (
        select(
            RevisionContentSnapshot.revision_id.label("revision_id"),
            func.max(RevisionContentSnapshot.sequence).label("sequence"),
        )
        .where(RevisionContentSnapshot.revision_id.in_(revision_ids))
        .group_by(RevisionContentSnapshot.revision_id)
        .subquery()
    )
    statement = (
        select(RevisionArtifactRelation.revision_id)
        .join(
            latest_sequences,
            latest_sequences.c.revision_id == RevisionArtifactRelation.revision_id,
        )
        .join(
            RevisionContentSnapshot,
            (RevisionContentSnapshot.revision_id == latest_sequences.c.revision_id)
            & (RevisionContentSnapshot.sequence == latest_sequences.c.sequence),
        )
        .join(
            RevisionContentSnapshotArtifact,
            (RevisionContentSnapshotArtifact.snapshot_id == RevisionContentSnapshot.id)
            & (RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id),
        )
        .join(Artifact, Artifact.id == RevisionArtifactRelation.artifact_id)
        .join(ArtifactFile, ArtifactFile.artifact_id == Artifact.id)
        .where(
            Artifact.artifact_type.in_(artifact_types),
            ArtifactFile.state.in_(states),
        )
        .distinct()
    )
    return set(db.scalars(statement))
