from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base
from modelryx.models.common import utcnow


class StagingSession(Base):
    """Versioned proposal that exists before managed engineering objects change."""

    __tablename__ = "staging_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_staging_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="HASHED", index=True)
    operation: Mapped[str] = mapped_column(String(32), default="CREATE_ITEM", index=True)
    snapshot_version: Mapped[int] = mapped_column(default=1)
    base_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True, nullable=True
    )
    base_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revision_content_snapshots.id"), nullable=True
    )
    package_sha256: Mapped[str] = mapped_column(String(64), index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    proposal_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    commit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    result_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('HASHED', 'METADATA_READY', 'REVIEW_REQUIRED', 'APPROVED', "
            "'PREPARING', 'READY_TO_COMMIT', 'COMMITTING', 'COMPLETED', 'FAILED', "
            "'CANCELLED', 'PROMOTED')",
            name="ck_staging_sessions_status",
        ),
        CheckConstraint(
            "operation IN ('CREATE_ITEM', 'CREATE_REVISION', 'EDIT_ITEM_REVISION', 'ADD_ARTIFACT', "
            "'CHECKIN_REVISION', 'CHECKIN_STRUCTURE', 'EDIT_ITEM_ATTACHMENT', "
            "'EDIT_REVISION_ATTACHMENT')",
            name="ck_staging_sessions_operation",
        ),
        CheckConstraint("snapshot_version >= 1", name="ck_staging_sessions_snapshot_version"),
    )


class StagingFile(Base):
    __tablename__ = "staging_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staging_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staging_sessions.id"), index=True
    )
    logical_path: Mapped[str] = mapped_column(String(1024))
    filename: Mapped[str] = mapped_column(String(255))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    state: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)

    __table_args__ = (
        UniqueConstraint("staging_id", "logical_path", name="uq_staging_files_logical_path"),
        CheckConstraint("state IN ('PENDING', 'VERIFIED')", name="ck_staging_files_state"),
    )
