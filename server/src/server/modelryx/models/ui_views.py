from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base
from modelryx.models.common import utcnow


class UiSavedView(Base):
    __tablename__ = "ui_saved_views"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    entity_type: Mapped[str] = mapped_column(String(32), default="LIBRARY")
    scope: Mapped[str] = mapped_column(String(16), default="PERSONAL")
    name: Mapped[str] = mapped_column(String(50))
    settings_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "entity_type", "scope", "name", name="uq_ui_saved_view_owner"),
        Index(
            "uq_ui_saved_view_shared_name", "entity_type", "name", unique=True,
            postgresql_where=text("scope = 'SHARED'"),
        ),
    )
