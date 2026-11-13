"""Identity of the schema installed by the fresh-database initializer."""

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base


class DatabaseSchema(Base):
    __tablename__ = "database_schema"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))

    __table_args__ = (CheckConstraint("id = 1", name="ck_database_schema_singleton"),)
