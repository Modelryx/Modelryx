from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
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


class RevisionComparison(Base):
    __tablename__ = "revision_comparisons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id"), index=True
    )
    from_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    to_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    from_fingerprint: Mapped[str] = mapped_column(String(64))
    to_fingerprint: Mapped[str] = mapped_column(String(64))
    comparison_engine_version: Mapped[str] = mapped_column(String(64))
    metadata_schema_version: Mapped[str] = mapped_column(String(64))
    rules_version: Mapped[str] = mapped_column(String(64))
    requested_domains_json: Mapped[list] = mapped_column(JSONB, default=list)
    summary_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'PARTIAL', 'COMPLETED', 'FAILED', 'STALE')",
            name="ck_revision_comparisons_status",
        ),
        CheckConstraint(
            "from_revision_id <> to_revision_id",
            name="ck_revision_comparisons_distinct_revisions",
        ),
    )


class RevisionComparisonDomain(Base):
    __tablename__ = "revision_comparison_domains"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comparison_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("revision_comparisons.id", ondelete="CASCADE"),
        index=True,
    )
    domain: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    engine_version: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    result_hash: Mapped[str] = mapped_column(String(64))
    change_count: Mapped[int] = mapped_column(default=0)
    warnings_json: Mapped[list] = mapped_column(JSONB, default=list)
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'UNCHANGED', 'UNSUPPORTED', 'FAILED')",
            name="ck_revision_comparison_domains_status",
        ),
        UniqueConstraint("comparison_id", "domain", name="uq_revision_comparison_domains_name"),
    )


class RevisionComparisonChange(Base):
    __tablename__ = "revision_comparison_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("revision_comparison_domains.id", ondelete="CASCADE"),
        index=True,
    )
    change_key: Mapped[str] = mapped_column(String(512))
    change_type: Mapped[str] = mapped_column(String(32), index=True)
    property_path: Mapped[str] = mapped_column(String(512))
    old_value_json: Mapped[object | None] = mapped_column(JSONB)
    new_value_json: Mapped[object | None] = mapped_column(JSONB)
    unit: Mapped[str | None] = mapped_column(String(32))
    delta: Mapped[float | None] = mapped_column(Float)
    percent_change: Mapped[float | None] = mapped_column(Float)
    significance: Mapped[str] = mapped_column(String(32), index=True)
    categories_json: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    provenance: Mapped[str] = mapped_column(String(32), default="EXACT")
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        CheckConstraint(
            "change_type IN ('ADDED', 'REMOVED', 'MODIFIED', 'UNCHANGED', 'MOVED', "
            "'RENAMED', 'REPLACED', 'UNKNOWN')",
            name="ck_revision_comparison_changes_type",
        ),
        CheckConstraint(
            "significance IN ('INFO', 'MINOR', 'MODERATE', 'MAJOR', 'CRITICAL')",
            name="ck_revision_comparison_changes_significance",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_revision_comparison_changes_confidence",
        ),
        UniqueConstraint("domain_id", "change_key", name="uq_revision_comparison_changes_key"),
    )


class RevisionComparisonExplanation(Base):
    __tablename__ = "revision_comparison_explanations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comparison_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("revision_comparisons.id", ondelete="CASCADE"),
        index=True,
    )
    comparison_result_hash: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(64))
    contract_version: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(255))
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    output_json: Mapped[dict] = mapped_column(JSONB)
    cited_change_keys_json: Mapped[list] = mapped_column(JSONB, default=list)
    cited_source_ids_json: Mapped[list] = mapped_column(JSONB, default=list)
    warnings_json: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "level IN ('QUICK', 'ENGINEERING', 'DETAILED')",
            name="ck_comparison_explanations_level",
        ),
        CheckConstraint("mode IN ('AI', 'DETERMINISTIC')", name="ck_comparison_explanations_mode"),
    )
