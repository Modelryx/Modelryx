"""Persist Phase 1A duplicate checks and explainable candidates."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_duplicate_detection_phase1a"
down_revision = "0021_metadata_search_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "duplicate_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("candidate_count_before_filter", sa.Integer(), nullable=False),
        sa.Column("candidate_count_after_filter", sa.Integer(), nullable=False),
        sa.Column("exact_match_found", sa.Boolean(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('COMPLETED', 'FAILED')", name="ck_duplicate_checks_status"),
        sa.ForeignKeyConstraint(["source_revision_id"], ["revisions.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index(
        "ix_duplicate_checks_source_revision_id", "duplicate_checks", ["source_revision_id"]
    )
    op.create_index("ix_duplicate_checks_status", "duplicate_checks", ["status"])
    op.create_table(
        "duplicate_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("duplicate_check_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("score_breakdown_json", postgresql.JSONB(), nullable=False),
        sa.Column("reasons_json", postgresql.JSONB(), nullable=False),
        sa.Column("differences_json", postgresql.JSONB(), nullable=False),
        sa.Column("recommended_action", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["duplicate_check_id"], ["duplicate_checks.id"]),
        sa.ForeignKeyConstraint(["candidate_revision_id"], ["revisions.id"]),
        sa.UniqueConstraint(
            "duplicate_check_id",
            "candidate_revision_id",
            name="uq_duplicate_candidates_check_revision",
        ),
    )
    op.create_index(
        "ix_duplicate_candidates_duplicate_check_id", "duplicate_candidates", ["duplicate_check_id"]
    )
    op.create_index(
        "ix_duplicate_candidates_candidate_revision_id",
        "duplicate_candidates",
        ["candidate_revision_id"],
    )
    op.create_index(
        "ix_duplicate_candidates_classification", "duplicate_candidates", ["classification"]
    )


def downgrade() -> None:
    op.drop_table("duplicate_candidates")
    op.drop_table("duplicate_checks")
