"""Create the current PostgreSQL schema for a fresh Modelryx installation.

This command never upgrades, drops, or resets an existing database.
"""

import hashlib
import json
import logging

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateIndex

from modelryx.core.db import Base, engine
from modelryx.models import DatabaseSchema, ItemNumberTemplate

LOGGER = logging.getLogger(__name__)
INITIALIZATION_LOCK = 72895643680769


def schema_fingerprint() -> str:
    """Hash declarative database rules in a process-independent order."""
    dialect = postgresql.dialect()
    tables = {}
    for name, table in sorted(Base.metadata.tables.items()):
        constraints = []
        for constraint in table.constraints:
            rule = {
                "kind": type(constraint).__name__,
                "name": constraint.name,
                "columns": [column.name for column in constraint.columns],
            }
            if isinstance(constraint, CheckConstraint):
                rule["expression"] = str(constraint.sqltext)
            if isinstance(constraint, ForeignKeyConstraint):
                rule["targets"] = [element.target_fullname for element in constraint.elements]
                rule["ondelete"] = constraint.ondelete
                rule["onupdate"] = constraint.onupdate
                rule["deferrable"] = constraint.deferrable
                rule["initially"] = constraint.initially
            constraints.append(json.dumps(rule, sort_keys=True))
        tables[name] = {
            "columns": [
                {
                    "name": column.name,
                    "type": column.type.compile(dialect=dialect),
                    "nullable": column.nullable,
                    "default": (
                        str(column.server_default.arg)
                        if column.server_default is not None
                        else None
                    ),
                }
                for column in table.columns
            ],
            "constraints": sorted(constraints),
            "indexes": sorted(
                str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes
            ),
        }
    return hashlib.sha256(json.dumps(tables, sort_keys=True).encode()).hexdigest()


def initialize_database(database_engine: Engine = engine) -> bool:
    """Return True for a new installation, False for the same initialized schema."""
    if database_engine.dialect.name != "postgresql":
        raise RuntimeError("Modelryx database initialization requires PostgreSQL with pgvector.")

    with database_engine.begin() as connection:
        # Serialize concurrent Compose/deployment initialization commands.
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": INITIALIZATION_LOCK})
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
        tables = set(inspect(connection).get_table_names(schema=schema))
        fingerprint = schema_fingerprint()
        if tables:
            if "database_schema" not in tables:
                raise RuntimeError(
                    "Database is not empty and has no current schema identity. "
                    "Use an empty database for this fresh-install release; existing schemas "
                    "are never upgraded or reset automatically."
                )
            installed = connection.execute(
                select(DatabaseSchema.fingerprint).where(DatabaseSchema.id == 1)
            ).scalar_one_or_none()
            if installed != fingerprint or tables != set(Base.metadata.tables):
                raise RuntimeError(
                    "Database schema does not match this application. Initialization cannot "
                    "upgrade an existing schema; use a matching release or an empty database."
                )
            return False

        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        Base.metadata.create_all(connection, checkfirst=False)
        connection.execute(
            ItemNumberTemplate.__table__.insert(),
            {
                "name": "Default numbering",
                "part_pattern": "PART-{sequence:06d}",
                "assembly_pattern": "ASM-{sequence:06d}",
                "part_next_value": 1,
                "assembly_next_value": 1,
                "is_enabled": True,
                "is_selected": True,
            },
        )
        connection.execute(DatabaseSchema.__table__.insert(), {"id": 1, "fingerprint": fingerprint})
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    created = initialize_database()
    LOGGER.info("Database initialized." if created else "Database schema is already current.")


if __name__ == "__main__":
    main()
