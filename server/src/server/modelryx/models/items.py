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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base
from modelryx.models.common import utcnow


class Item(Base):
    __tablename__ = "parts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    part_type: Mapped[str] = mapped_column(String(32), default="PART", index=True)
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("revisions.id", name="fk_parts_current_revision", use_alter=True),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("part_type IN ('PART', 'ASSEMBLY')", name="ck_parts_part_type"),
    )


class ItemNumberTemplate(Base):
    """Named numbering scheme with independent Part and Assembly counters."""

    __tablename__ = "item_number_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    part_pattern: Mapped[str] = mapped_column(String(100))
    assembly_pattern: Mapped[str] = mapped_column(String(100))
    part_next_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    assembly_next_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("part_next_value > 0", name="ck_item_number_templates_part_positive"),
        CheckConstraint(
            "assembly_next_value > 0", name="ck_item_number_templates_assembly_positive"
        ),
    )


# Transitional source-code alias. ``Item`` is the public domain term; the existing
# table and attribute names remain stable so installations can upgrade without a
# disruptive data rewrite.
Part = Item


class Revision(Base):
    __tablename__ = "revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id"), index=True
    )
    revision_code: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    cad_system: Mapped[str] = mapped_column(String(100))
    native_format: Mapped[str] = mapped_column(String(50))
    metadata_json: Mapped[dict] = mapped_column(JSONB)
    derived_metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    search_text: Mapped[str] = mapped_column(Text, default="")
    package_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(24), default="WORKING", index=True)
    metadata_status: Mapped[str] = mapped_column(String(32), default="UNAVAILABLE", index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_metadata_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "metadata_extractions.id",
            name="fk_revisions_current_metadata_extraction",
            use_alter=True,
        ),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('DRAFT', 'UPLOADING', 'VALIDATING', 'COMMITTED', "
            "'PROCESSING', 'READY', 'READY_WITH_WARNINGS', 'FAILED')",
            name="ck_revisions_state",
        ),
        CheckConstraint(
            "metadata_status IN ('UNAVAILABLE', 'READY', 'PARTIAL', 'INVALID')",
            name="ck_revisions_metadata_status",
        ),
        CheckConstraint(
            "lifecycle_state IN ('WORKING', 'RELEASED', 'SUPERSEDED', 'OBSOLETE')",
            name="ck_revisions_lifecycle_state",
        ),
        UniqueConstraint("part_id", "revision_code", name="uq_revisions_part_code"),
    )
