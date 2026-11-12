"""Add PostgreSQL full-text search indexes."""

from alembic import op

revision = "0005_search_indexes"
down_revision = "0004_processing_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_parts_search_vector ON parts USING GIN "
        "(to_tsvector('simple', part_number || ' ' || name || ' ' || "
        "coalesce(description, '') || ' ' || coalesce(category, '')))"
    )
    op.execute(
        "CREATE INDEX ix_revisions_search_vector ON revisions USING GIN "
        "(to_tsvector('simple', search_text))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_revisions_search_vector")
    op.execute("DROP INDEX ix_parts_search_vector")
