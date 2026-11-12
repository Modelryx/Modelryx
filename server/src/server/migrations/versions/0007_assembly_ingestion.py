"""Add grouped assembly ingestion and verified-native hash lookup."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_assembly_ingestion"
down_revision = "0006_phase1_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parts", sa.Column("part_type", sa.String(32), nullable=False, server_default="PART")
    )
    op.create_check_constraint("ck_parts_part_type", "parts", "part_type IN ('PART', 'ASSEMBLY')")
    op.create_index("ix_parts_part_type", "parts", ["part_type"])
    op.create_index(
        "ix_artifacts_verified_native_sha256",
        "artifacts",
        ["sha256", "byte_size"],
        postgresql_where=sa.text("kind = 'NATIVE' AND state = 'VERIFIED'"),
    )

    op.create_table(
        "assembly_checkins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_checkin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("client_checkin_id"),
        sa.CheckConstraint(
            "state IN ('UPLOADING', 'VALIDATING', 'COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED')",
            name="ck_assembly_checkins_state",
        ),
    )
    op.create_index("ix_assembly_checkins_state", "assembly_checkins", ["state"])
    op.create_index("ix_assembly_checkins_expires_at", "assembly_checkins", ["expires_at"])

    op.create_table(
        "assembly_checkin_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "assembly_checkin_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assembly_checkins.id"),
            nullable=False,
        ),
        sa.Column("logical_path", sa.String(1024), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "part_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parts.id"),
            nullable=False,
        ),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "working_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("working_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=False,
        ),
        sa.Column("reused", sa.Boolean(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.CheckConstraint("role IN ('MAIN', 'COMPONENT')", name="ck_assembly_files_role"),
        sa.CheckConstraint(
            "state IN ('PENDING', 'UPLOADED', 'VERIFIED', 'REUSED', 'CANCELLED')",
            name="ck_assembly_files_state",
        ),
        sa.UniqueConstraint(
            "assembly_checkin_id", "logical_path", name="uq_assembly_files_logical_path"
        ),
    )
    op.create_index(
        "ix_assembly_checkin_files_assembly_checkin_id",
        "assembly_checkin_files",
        ["assembly_checkin_id"],
    )
    op.create_index("ix_assembly_checkin_files_state", "assembly_checkin_files", ["state"])


def downgrade() -> None:
    op.drop_table("assembly_checkin_files")
    op.drop_table("assembly_checkins")
    op.drop_index("ix_artifacts_verified_native_sha256", table_name="artifacts")
    op.drop_index("ix_parts_part_type", table_name="parts")
    op.drop_constraint("ck_parts_part_type", "parts", type_="check")
    op.drop_column("parts", "part_type")
