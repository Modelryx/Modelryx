"""Persist citation-safe Phase 5.6 comparison explanations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_phase56_explanations"
down_revision = "0034_phase50_comparisons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revision_comparison_explanations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "comparison_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revision_comparisons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("comparison_result_hash", sa.String(64), nullable=False),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(255)),
        sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
        sa.Column("output_json", postgresql.JSONB(), nullable=False),
        sa.Column("cited_change_keys_json", postgresql.JSONB(), nullable=False),
        sa.Column("cited_source_ids_json", postgresql.JSONB(), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "level IN ('QUICK', 'ENGINEERING', 'DETAILED')",
            name="ck_comparison_explanations_level",
        ),
        sa.CheckConstraint(
            "mode IN ('AI', 'DETERMINISTIC')",
            name="ck_comparison_explanations_mode",
        ),
    )
    op.create_index(
        "ix_revision_comparison_explanations_comparison_id",
        "revision_comparison_explanations",
        ["comparison_id"],
    )
    op.create_index(
        "ix_revision_comparison_explanations_cache_key",
        "revision_comparison_explanations",
        ["cache_key"],
    )


def downgrade() -> None:
    op.drop_table("revision_comparison_explanations")
