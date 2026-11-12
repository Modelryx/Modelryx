from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base
from modelryx.models.common import utcnow


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        "part_id", UUID(as_uuid=True), ForeignKey("parts.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    attachment_type: Mapped[str] = mapped_column(String(32), default="OTHER", index=True)
    description: Mapped[str | None] = mapped_column(Text)
    lifecycle_state: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "attachment_type IN ('DRAWING', 'DOCUMENT', 'SPECIFICATION', 'IMAGE', "
            "'SPREADSHEET', 'CERTIFICATE', 'OTHER')",
            name="ck_attachments_type",
        ),
        CheckConstraint(
            "lifecycle_state IN ('ACTIVE', 'ARCHIVED')", name="ck_attachments_lifecycle"
        ),
    )


class AdditionalFileStage(Base):
    """Reviewable Item-level Additional File change committed atomically."""

    __tablename__ = "additional_file_stages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_staging_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id"), index=True
    )
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True, nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staging_sessions.id"), index=True, nullable=True
    )
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attachments.id"), index=True, nullable=True
    )
    base_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    content_blob_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_blobs.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(24))
    duplicate_action: Mapped[str] = mapped_column(String(24), default="UPLOAD")
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    name: Mapped[str] = mapped_column(String(255))
    attachment_type: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(255))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    commit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    result_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "action IN ('CREATE', 'NEW_VERSION', 'REMOVE')", name="ck_additional_file_stage_action"
        ),
        CheckConstraint(
            "duplicate_action IN ('UPLOAD', 'REUSE_EXISTING')",
            name="ck_additional_file_stage_duplicate_action",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'READY', 'COMPLETED', 'CANCELLED')",
            name="ck_additional_file_stage_status",
        ),
        CheckConstraint("byte_size > 0", name="ck_additional_file_stage_byte_size"),
    )


class AttachmentCheckout(Base):
    """Independent edit lease for Item-level attachment content."""

    __tablename__ = "attachment_checkouts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id"), index=True
    )
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attachments.id"), unique=True, index=True, nullable=True
    )
    base_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True, nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staging_sessions.id"), index=True, nullable=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
