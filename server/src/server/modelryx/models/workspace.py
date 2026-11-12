from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
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


class Checkout(Base):
    __tablename__ = "checkouts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id"), index=True
    )
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), unique=True, index=True
    )
    base_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revision_content_snapshots.id")
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    checkout_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, index=True
    )
    root_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    scope: Mapped[str] = mapped_column(String(16), default="SINGLE")
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        CheckConstraint("scope IN ('SINGLE', 'STRUCTURE')", name="ck_checkouts_scope"),
    )


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revision_content_snapshots.id"), index=True
    )
    type: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="QUEUED")
    progress_percent: Mapped[int] = mapped_column(default=0)
    converter_version: Mapped[str | None] = mapped_column(String(64))
    cache_key: Mapped[str | None] = mapped_column(String(128), index=True)
    diagnostics_json: Mapped[dict | None] = mapped_column(JSONB)
    attempt_count: Mapped[int] = mapped_column(default=0)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    error_detail: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_processing_jobs_state",
        ),
    )


class DownloadBundle(Base):
    __tablename__ = "download_bundles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revision_content_snapshots.id"), index=True
    )
    processing_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id"), unique=True
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_files.id")
    )
    state: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    source_signature: Mapped[str | None] = mapped_column(String(64), index=True)
    revision_snapshots_json: Mapped[dict | None] = mapped_column(JSONB)
    workspace_state: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_detail: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'READY', 'FAILED')",
            name="ck_download_bundles_state",
        ),
        CheckConstraint(
            "workspace_state IN ('ACTIVE', 'ARCHIVED', 'REMOVED')",
            name="ck_download_bundles_workspace_state",
        ),
    )
