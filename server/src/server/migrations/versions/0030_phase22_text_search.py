"""Add Phase 2.2 English full-text indexes for engineering search."""

from alembic import op

revision = "0030_phase22_text_search"
down_revision = "0029_revision_package_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_parts_engineering_search_en ON parts USING GIN "
        "((setweight(to_tsvector('english', coalesce(part_number, '')), 'A') || "
        "setweight(to_tsvector('english', coalesce(name, '')), 'A') || "
        "setweight(to_tsvector('english', coalesce(category, '')), 'B') || "
        "setweight(to_tsvector('english', coalesce(description, '')), 'B')))"
    )
    op.execute(
        "CREATE INDEX ix_revisions_engineering_search_en ON revisions USING GIN "
        "(to_tsvector('english', coalesce(search_text, '')))"
    )
    op.execute(
        "CREATE INDEX ix_metadata_extractions_engineering_search_en "
        "ON metadata_extractions USING GIN "
        "(to_tsvector('english', metadata_json::text || ' ' || derived_json::text))"
    )
    op.execute(
        "CREATE INDEX ix_artifacts_filename_search_en ON artifacts USING GIN "
        "(to_tsvector('english', coalesce(original_filename, '')))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_artifacts_filename_search_en")
    op.execute("DROP INDEX ix_metadata_extractions_engineering_search_en")
    op.execute("DROP INDEX ix_revisions_engineering_search_en")
    op.execute("DROP INDEX ix_parts_engineering_search_en")
