from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base
from modelryx.models.common import utcnow


class CADSession(Base):
    """Owned isolated copy of one authoritative native CAD artifact."""

    __tablename__ = "cad_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_files.id"), index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    worker_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), default="CREATED", index=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    workspace_key: Mapped[str] = mapped_column(String(1024), unique=True)
    lock_version: Mapped[int] = mapped_column(default=0)
    error_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED', 'OPENING', 'READY', 'EXECUTING', 'VERIFYING', "
            "'READY_TO_REVIEW', 'COMMITTED', 'FAILED', 'DISCARDED', 'CANCELLED', 'EXPIRED')",
            name="ck_cad_sessions_status",
        ),
    )


class CADOperationPlan(Base):
    """Exact immutable semantic CAD parameter edit awaiting confirmation."""

    __tablename__ = "cad_operation_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cad_sessions.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    risk: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    operation: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str] = mapped_column(String(255))
    parameter: Mapped[str] = mapped_column(String(100))
    expected_value_json: Mapped[dict] = mapped_column(JSONB)
    new_value_json: Mapped[dict] = mapped_column(JSONB)
    unit: Mapped[str | None] = mapped_column(String(16))
    preview_json: Mapped[dict] = mapped_column(JSONB)
    state_token: Mapped[str] = mapped_column(String(64), index=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'EXECUTING', 'SUCCEEDED', 'FAILED', "
            "'CANCELLED', 'EXPIRED', 'STALE')",
            name="ck_cad_operation_plans_status",
        ),
        CheckConstraint("risk IN ('MEDIUM')", name="ck_cad_operation_plans_risk"),
        CheckConstraint(
            "operation IN ('feature.parameter.set_value', 'sketch.constraint.set_value', "
            "'material.set', 'sketch.create', 'feature.create_pad', "
            "'feature.create_pocket', 'feature.create_hole', 'feature.create_fillet')",
            name="ck_cad_operation_plans_operation",
        ),
    )


class CADExecution(Base):
    """Idempotent queued execution of one confirmed CAD plan."""

    __tablename__ = "cad_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cad_operation_plans.id"), unique=True, index=True
    )
    processing_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    result_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_cad_executions_status",
        ),
    )


class CADCommitPlan(Base):
    """Second confirmation binding a verified CAD result to a new revision."""

    __tablename__ = "cad_commit_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cad_sessions.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    revision_code: Mapped[str] = mapped_column(String(50))
    state_token: Mapped[str] = mapped_column(String(64), index=True)
    preview_json: Mapped[dict] = mapped_column(JSONB)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    committed_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'COMMITTED', 'CANCELLED', 'EXPIRED', 'STALE', 'FAILED')",
            name="ck_cad_commit_plans_status",
        ),
    )
