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


class ApplicationSetting(Base):
    """Administrator-managed runtime configuration that is safe to persist."""

    __tablename__ = "application_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(2000))
    updated_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkerHeartbeat(Base):
    """Latest liveness and compatibility report from a CAD worker agent."""

    __tablename__ = "worker_heartbeats"

    worker_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    machine_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    worker_version: Mapped[str] = mapped_column(String(100))
    application_version: Mapped[str | None] = mapped_column(String(100))
    adapter_version: Mapped[str | None] = mapped_column(String(100))
    compatibility_state: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    capabilities_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="IDLE")
    current_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detail: Mapped[str | None] = mapped_column(String(500))
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('IDLE', 'BUSY', 'DEGRADED')", name="ck_worker_heartbeats_status"
        ),
        CheckConstraint(
            "compatibility_state IN ('COMPATIBLE', 'INCOMPATIBLE', 'UNKNOWN')",
            name="ck_worker_heartbeats_compatibility",
        ),
    )
