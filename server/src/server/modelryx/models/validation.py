from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base
from modelryx.models.common import utcnow


class ValidationRun(Base):
    """Immutable deterministic validation result for one staging snapshot."""

    __tablename__ = "validation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staging_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staging_sessions.id"), index=True
    )
    snapshot_version: Mapped[int] = mapped_column()
    run_number: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), index=True)
    report_hash: Mapped[str] = mapped_column(String(64), index=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    summary_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    validator_states_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("staging_id", "run_number", name="uq_validation_runs_staging_number"),
        CheckConstraint("snapshot_version >= 1", name="ck_validation_runs_snapshot_version"),
        CheckConstraint(
            "status IN ('READY', 'READY_WITH_WARNINGS', 'BLOCKED', 'ERROR')",
            name="ck_validation_runs_status",
        ),
    )


class ValidationFinding(Base):
    """Normalized, auditable validator output; AI never mutates these fields."""

    __tablename__ = "validation_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("validation_runs.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    state: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    source_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    location_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    details_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    actions_json: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'ERROR', 'CRITICAL')",
            name="ck_validation_findings_severity",
        ),
        CheckConstraint(
            "state IN ('ACTIVE', 'RESOLVED', 'IGNORED', 'SUPERSEDED')",
            name="ck_validation_findings_state",
        ),
    )


class ValidationAIAnalysis(Base):
    """Schema-checked explanation cached independently of deterministic validation."""

    __tablename__ = "validation_ai_analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("validation_runs.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(64))
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    output_json: Mapped[dict] = mapped_column(JSONB)
    warnings_json: Mapped[list] = mapped_column(JSONB, default=list)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("mode IN ('AI', 'DETERMINISTIC')", name="ck_validation_ai_mode"),
    )


class ValidationFindingAction(Base):
    """Append-only review acknowledgement for a non-blocking warning."""

    __tablename__ = "validation_finding_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("validation_findings.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(32), default="ACKNOWLEDGED")
    comment: Mapped[str | None] = mapped_column(String(500))
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("action IN ('ACKNOWLEDGED')", name="ck_validation_finding_actions_action"),
    )
