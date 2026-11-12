"""Add pre-creation file staging and allow staging-owned duplicate checks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_precreation_staging"
down_revision = "0025_duplicate_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staging_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_staging_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("package_sha256", sa.String(64), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('HASHED', 'REVIEW_REQUIRED', 'APPROVED', 'CANCELLED', 'PROMOTED')",
            name="ck_staging_sessions_status",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index("ix_staging_sessions_status", "staging_sessions", ["status"])
    op.create_index("ix_staging_sessions_package_sha256", "staging_sessions", ["package_sha256"])
    op.create_table(
        "staging_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("staging_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_path", sa.String(1024), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["staging_id"], ["staging_sessions.id"]),
        sa.UniqueConstraint("staging_id", "logical_path", name="uq_staging_files_logical_path"),
    )
    op.create_index("ix_staging_files_staging_id", "staging_files", ["staging_id"])
    op.create_index("ix_staging_files_sha256", "staging_files", ["sha256"])
    op.alter_column("duplicate_checks", "source_revision_id", nullable=True)
    op.add_column(
        "duplicate_checks",
        sa.Column("source_staging_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_duplicate_checks_source_staging",
        "duplicate_checks",
        "staging_sessions",
        ["source_staging_id"],
        ["id"],
    )
    op.create_index(
        "ix_duplicate_checks_source_staging_id", "duplicate_checks", ["source_staging_id"]
    )
    op.create_check_constraint(
        "ck_duplicate_checks_one_source",
        "duplicate_checks",
        "(source_revision_id IS NOT NULL) <> (source_staging_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_duplicate_checks_one_source", "duplicate_checks", type_="check")
    op.drop_index("ix_duplicate_checks_source_staging_id", table_name="duplicate_checks")
    op.drop_constraint("fk_duplicate_checks_source_staging", "duplicate_checks", type_="foreignkey")
    op.drop_column("duplicate_checks", "source_staging_id")
    op.alter_column("duplicate_checks", "source_revision_id", nullable=False)
    op.drop_table("staging_files")
    op.drop_table("staging_sessions")
