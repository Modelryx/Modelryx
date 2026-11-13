"""Exercise fresh initialization on isolated schemas in the test PostgreSQL database."""

import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from modelryx.core.db import Base, engine
from modelryx.initialize_database import initialize_database, schema_fingerprint
from modelryx.models import DatabaseSchema, Item, ItemNumberTemplate, User
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def empty_database():
    schema = f"init_test_{uuid.uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = create_engine(engine.url, connect_args={"options": f"-csearch_path={schema},public"})
    try:
        yield isolated
    finally:
        isolated.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def table_names(database_engine):
    with database_engine.connect() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
        return inspect(connection).get_table_names(schema=schema)


def test_initialization_creates_current_schema_and_numbering(empty_database):
    assert initialize_database(empty_database) is True
    assert set(table_names(empty_database)) == set(Base.metadata.tables)
    with empty_database.connect() as connection:
        assert connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        ).scalar_one()
        template = connection.execute(select(ItemNumberTemplate.__table__)).mappings().one()
        assert template["is_selected"] and template["is_enabled"]
        assert template["part_pattern"] == "PART-{sequence:06d}"
        assert template["assembly_pattern"] == "ASM-{sequence:06d}"
        assert template["part_next_value"] == template["assembly_next_value"] == 1
        assert connection.execute(select(DatabaseSchema.fingerprint)).scalar_one() == (
            schema_fingerprint()
        )


def test_reinitialization_preserves_configuration_and_counters(empty_database):
    initialize_database(empty_database)
    with empty_database.begin() as connection:
        connection.execute(
            ItemNumberTemplate.__table__.update().values(part_next_value=42, is_enabled=False)
        )
    assert initialize_database(empty_database) is False
    with empty_database.connect() as connection:
        row = connection.execute(select(ItemNumberTemplate.__table__)).mappings().one()
        assert row["part_next_value"] == 42
        assert row["is_enabled"] is False


def test_nonempty_unrecognized_database_is_not_modified(empty_database):
    with empty_database.begin() as connection:
        connection.execute(text("CREATE TABLE existing_data (value text)"))
        connection.execute(text("INSERT INTO existing_data VALUES ('preserve me')"))
    with pytest.raises(RuntimeError, match="not empty"):
        initialize_database(empty_database)
    with empty_database.connect() as connection:
        assert connection.execute(text("SELECT value FROM existing_data")).scalar_one() == (
            "preserve me"
        )
    assert table_names(empty_database) == ["existing_data"]


def test_incompatible_schema_is_not_upgraded(empty_database):
    initialize_database(empty_database)
    with empty_database.begin() as connection:
        connection.execute(DatabaseSchema.__table__.update().values(fingerprint="0" * 64))
    with pytest.raises(RuntimeError, match="does not match"):
        initialize_database(empty_database)
    with empty_database.connect() as connection:
        assert connection.execute(select(DatabaseSchema.fingerprint)).scalar_one() == "0" * 64


def test_incomplete_schema_is_rejected(empty_database):
    initialize_database(empty_database)
    with empty_database.begin() as connection:
        connection.execute(text("DROP TABLE ui_saved_views"))
    with pytest.raises(RuntimeError, match="does not match"):
        initialize_database(empty_database)
    assert "ui_saved_views" not in table_names(empty_database)


def test_initialization_failure_rolls_back_tables(empty_database, monkeypatch):
    create_all = Base.metadata.create_all

    def fail_after_ddl(connection, **kwargs):
        create_all(connection, **kwargs)
        raise RuntimeError("simulated initialization failure")

    monkeypatch.setattr(Base.metadata, "create_all", fail_after_ddl)
    with pytest.raises(RuntimeError, match="simulated"):
        initialize_database(empty_database)
    assert table_names(empty_database) == []


def test_concurrent_initializers_create_only_one_installation(empty_database):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: initialize_database(empty_database), range(2)))
    assert sorted(results) == [False, True]
    with empty_database.connect() as connection:
        assert len(connection.execute(select(ItemNumberTemplate.__table__)).all()) == 1


def test_fingerprint_is_stable_across_processes():
    actual = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from modelryx.initialize_database import schema_fingerprint; "
            "print(schema_fingerprint())",
        ],
        text=True,
    ).strip()
    assert actual == schema_fingerprint()


def test_partial_and_casefold_unique_indexes_are_enforced(empty_database):
    initialize_database(empty_database)
    with empty_database.begin() as connection:
        user_id = connection.execute(
            User.__table__.insert()
            .values(external_subject="initializer", display_name="Test", role="administrator")
            .returning(User.id)
        ).scalar_one()
        connection.execute(
            Item.__table__.insert().values(part_number="PART-123", name="Test", created_by=user_id)
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                Item.__table__.insert().values(
                    part_number="part-123", name="Duplicate", created_by=user_id
                )
            )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                ItemNumberTemplate.__table__.insert().values(
                    name="Second selected template",
                    part_pattern="P-{sequence}",
                    assembly_pattern="A-{sequence}",
                    is_selected=True,
                )
            )


def test_search_and_workflow_indexes_exist(empty_database):
    initialize_database(empty_database)
    expected = {
        "ai_documents": {"ix_ai_documents_content_fts", "ix_ai_documents_embedding_hnsw"},
        "knowledge_chunks": {
            "ix_knowledge_chunks_content_fts",
            "ix_knowledge_chunks_embedding_hnsw",
        },
        "parts": {"ix_parts_engineering_search_en", "uq_parts_part_number_casefold"},
        "checkouts": {"uq_checkouts_unversioned_item"},
        "attachment_checkouts": {"uq_attachment_checkouts_item_create"},
        "cad_sessions": {"uq_cad_sessions_active_revision"},
        "revision_approval_requests": {"uq_revision_approval_requests_pending"},
    }
    inspector = inspect(empty_database)
    for table, names in expected.items():
        assert names <= {index["name"] for index in inspector.get_indexes(table)}
