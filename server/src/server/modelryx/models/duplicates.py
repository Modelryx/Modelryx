from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base
from modelryx.models.common import utcnow


class DuplicateCheck(Base):
    """A reproducible duplicate-detection run for one revision."""

    __tablename__ = "duplicate_checks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    source_staging_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staging_sessions.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", index=True)
    algorithm_version: Mapped[str] = mapped_column(String(64))
    candidate_count_before_filter: Mapped[int] = mapped_column(default=0)
    candidate_count_after_filter: Mapped[int] = mapped_column(default=0)
    exact_match_found: Mapped[bool] = mapped_column(Boolean, default=False)
    total_duration_ms: Mapped[float] = mapped_column(Float, default=0)
    metadata_duration_ms: Mapped[float] = mapped_column(Float, default=0)
    vector_duration_ms: Mapped[float] = mapped_column(Float, default=0)
    ai_duration_ms: Mapped[float] = mapped_column(Float, default=0)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('COMPLETED', 'FAILED')", name="ck_duplicate_checks_status"),
    )


class DuplicateCandidate(Base):
    """Explainable score and classification retained for review and tuning."""

    __tablename__ = "duplicate_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    duplicate_check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("duplicate_checks.id"), index=True
    )
    candidate_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    classification: Mapped[str] = mapped_column(String(32), index=True)
    score_breakdown_json: Mapped[dict] = mapped_column(JSONB)
    reasons_json: Mapped[list] = mapped_column(JSONB, default=list)
    differences_json: Mapped[list] = mapped_column(JSONB, default=list)
    recommended_action: Mapped[str] = mapped_column(String(32))
    ai_result_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "duplicate_check_id",
            "candidate_revision_id",
            name="uq_duplicate_candidates_check_revision",
        ),
        CheckConstraint(
            "(source_revision_id IS NOT NULL) <> (source_staging_id IS NOT NULL)",
            name="ck_duplicate_checks_one_source",
        ),
    )


class DuplicateFeedback(Base):
    """Authoritative human disposition for a duplicate-check result."""

    __tablename__ = "duplicate_feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    duplicate_check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("duplicate_checks.id"), unique=True, index=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("duplicate_candidates.id"), index=True
    )
    user_decision: Mapped[str] = mapped_column(String(32), index=True)
    system_classification: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "user_decision IN ('USE_EXISTING', 'CREATE_NEW', 'NOT_DUPLICATE')",
            name="ck_duplicate_feedback_decision",
        ),
    )
