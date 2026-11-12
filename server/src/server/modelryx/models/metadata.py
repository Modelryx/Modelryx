from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base
from modelryx.models.common import utcnow


class MetadataExtraction(Base):
    """Immutable worker extraction and normalized PartMetadata for one revision."""

    __tablename__ = "metadata_extractions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revision_content_snapshots.id"), index=True
    )
    raw_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_files.id")
    )
    schema_version: Mapped[str] = mapped_column(String(32))
    worker_type: Mapped[str] = mapped_column(String(100))
    worker_version: Mapped[str] = mapped_column(String(100))
    source_system: Mapped[str] = mapped_column(String(100))
    source_system_version: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), index=True)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column(JSONB)
    derived_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    material: Mapped[str | None] = mapped_column(String(255), index=True)
    bbox_x_mm: Mapped[float] = mapped_column(Float)
    bbox_y_mm: Mapped[float] = mapped_column(Float)
    bbox_z_mm: Mapped[float] = mapped_column(Float)
    bbox_min_mm: Mapped[float] = mapped_column(Float)
    bbox_mid_mm: Mapped[float] = mapped_column(Float)
    bbox_max_mm: Mapped[float] = mapped_column(Float, index=True)
    volume_mm3: Mapped[float] = mapped_column(Float, index=True)
    surface_area_mm2: Mapped[float] = mapped_column(Float)
    mass_kg: Mapped[float | None] = mapped_column(Float)
    density_kg_m3: Mapped[float | None] = mapped_column(Float)
    hole_diameter_min_mm: Mapped[float | None] = mapped_column(Float)
    hole_diameter_max_mm: Mapped[float | None] = mapped_column(Float)
    drawing_count: Mapped[int | None] = mapped_column()
    tolerance_count: Mapped[int | None] = mapped_column()
    instance_count: Mapped[int | None] = mapped_column()
    solid_count: Mapped[int | None] = mapped_column()
    face_count: Mapped[int | None] = mapped_column()
    edge_count: Mapped[int | None] = mapped_column()
    hole_count: Mapped[int | None] = mapped_column()
    fillet_count: Mapped[int | None] = mapped_column()
    chamfer_count: Mapped[int | None] = mapped_column()
    extrusion_count: Mapped[int | None] = mapped_column()
    revolution_count: Mapped[int | None] = mapped_column()
    pocket_count: Mapped[int | None] = mapped_column()
    pattern_count: Mapped[int | None] = mapped_column()
    thread_count: Mapped[int | None] = mapped_column()
    warnings_json: Mapped[list] = mapped_column(JSONB, default=list)
    errors_json: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('READY', 'PARTIAL', 'INVALID')",
            name="ck_metadata_extractions_status",
        ),
    )
