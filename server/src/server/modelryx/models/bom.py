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


class BomViewRevision(Base):
    """Immutable assembly structure committed for one revision content snapshot."""

    __tablename__ = "bom_view_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revision_content_snapshots.id"), unique=True
    )
    sequence: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("revision_id", "sequence", name="uq_bom_view_revisions_sequence"),
        CheckConstraint("sequence >= 1", name="ck_bom_view_revisions_sequence"),
    )


class BomLine(Base):
    """One resolved child ItemRevision occurrence in a committed assembly BOM."""

    __tablename__ = "bom_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bom_view_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bom_view_revisions.id", ondelete="CASCADE"), index=True
    )
    parent_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    child_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    occurrence_key: Mapped[str] = mapped_column(String(255))
    source_reference: Mapped[str] = mapped_column(String(500), default="")
    reference_designator: Mapped[str | None] = mapped_column(String(255))
    transform: Mapped[list[float]] = mapped_column(
        JSONB,
        default=lambda: [
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ],
    )
    is_suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    properties_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    quantity: Mapped[int] = mapped_column(default=1)
    sequence: Mapped[int] = mapped_column(default=0)

    __table_args__ = (
        UniqueConstraint(
            "bom_view_revision_id",
            "parent_revision_id",
            "occurrence_key",
            name="uq_bom_lines_occurrence",
        ),
        CheckConstraint("quantity > 0", name="ck_bom_lines_quantity"),
        CheckConstraint("sequence >= 0", name="ck_bom_lines_sequence"),
    )


class RevisionRelationship(Base):
    __tablename__ = "revision_relationships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    target_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    target_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id"), index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint(
            (
                "relationship_type IN "
                "('SPECIFICATION', 'MATERIAL_SPEC', 'REFERENCE', 'TEST_PROCEDURE')"
            ),
            name="ck_revision_relationships_type",
        ),
        CheckConstraint(
            "target_revision_id IS NOT NULL OR target_item_id IS NOT NULL",
            name="ck_revision_relationships_target",
        ),
        UniqueConstraint(
            "source_revision_id",
            "target_revision_id",
            "target_item_id",
            "relationship_type",
            name="uq_revision_relationships_target_type",
        ),
    )
