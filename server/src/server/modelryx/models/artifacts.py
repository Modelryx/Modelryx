from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base
from modelryx.models.common import utcnow


class Artifact(Base):
    """Logical managed-content container attached to an ItemRevision by relation."""

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, index=True
    )
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attachments.id"), nullable=True, index=True
    )
    client_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=True, index=True
    )
    generation: Mapped[int] = mapped_column(default=1)
    artifact_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(32), default="USER", index=True)
    derived_from_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    generator: Mapped[str | None] = mapped_column(String(100))
    generator_version: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(24), default="READY", index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("artifact_key", "generation", name="uq_artifacts_key_generation"),
        CheckConstraint("generation >= 1", name="ck_artifacts_generation"),
        CheckConstraint(
            "classification IN ('AUTHORITATIVE', 'DERIVED', 'EXTERNAL_REFERENCE')",
            name="ck_artifacts_classification",
        ),
        CheckConstraint(
            "source IN ('USER', 'WORKER', 'IMPORT', 'SYSTEM')", name="ck_artifacts_source"
        ),
        CheckConstraint(
            "state IN ('UPLOADING', 'READY', 'PROCESSING', 'FAILED', 'SUPERSEDED', "
            "'CANCELLED', 'EXPIRED')",
            name="ck_artifacts_state",
        ),
        CheckConstraint(
            "classification != 'DERIVED' OR derived_from_artifact_id IS NOT NULL",
            name="ck_artifacts_derived_source",
        ),
    )


class ArtifactFile(Base):
    """Named file reference owned by an Artifact.

    The revision/working-version/kind fields are temporary adapters for the
    pre-Artifact write paths and will be removed before first deployment.
    """

    __tablename__ = "artifact_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), index=True, nullable=True
    )
    content_blob_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_blobs.id"), index=True, nullable=True
    )
    reference_name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str | None] = mapped_column(String(24))
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(255))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32))
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("artifact_id", "reference_name", name="uq_artifact_files_reference"),
        CheckConstraint("byte_size >= 0", name="ck_artifact_files_byte_size"),
        CheckConstraint(
            "role IS NULL OR role IN ('PRIMARY', 'SECONDARY', 'AUXILIARY')",
            name="ck_artifact_files_role",
        ),
    )


class ContentBlob(Base):
    """Verified immutable bytes shared by otherwise independent file records."""

    __tablename__ = "content_blobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    state: Mapped[str] = mapped_column(String(32), default="VERIFIED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("sha256", "byte_size", name="uq_content_blobs_digest_size"),
        CheckConstraint("byte_size >= 0", name="ck_content_blobs_byte_size"),
        CheckConstraint("state IN ('VERIFIED', 'QUARANTINED')", name="ck_content_blobs_state"),
    )


class RevisionArtifactRelation(Base):
    __tablename__ = "revision_artifact_relations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(32), index=True)
    sequence: Mapped[int] = mapped_column(default=0)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "revision_id", "artifact_id", "relation_type", name="uq_revision_artifact_relation"
        ),
        CheckConstraint("sequence >= 0", name="ck_revision_artifact_relations_sequence"),
        CheckConstraint(
            "relation_type IN ('SPECIFICATION', 'REFERENCE', 'DRAWING', 'REPRESENTATION', "
            "'THUMBNAIL', 'ANALYSIS', 'ATTACHMENT')",
            name="ck_revision_artifact_relations_type",
        ),
    )


class RevisionContentSnapshot(Base):
    __tablename__ = "revision_content_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    sequence: Mapped[int] = mapped_column()
    commit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("revision_id", "sequence", name="uq_revision_content_snapshot_sequence"),
        CheckConstraint("sequence >= 1", name="ck_revision_content_snapshots_sequence"),
    )


class RevisionContentSnapshotArtifact(Base):
    __tablename__ = "revision_content_snapshot_artifacts"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revision_content_snapshots.id"), primary_key=True
    )
    relation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revision_artifact_relations.id"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(default=0)

    __table_args__ = (CheckConstraint("sequence >= 0", name="ck_snapshot_artifacts_sequence"),)
