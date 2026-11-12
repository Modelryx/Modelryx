"""Retire superseded assembly and structure check-in persistence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0059_retire_split_sessions"
down_revision = "0058_checkout_snapshot_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("assembly_occurrences")
    op.drop_table("assembly_checkin_files")
    op.drop_table("assembly_checkins")
    op.drop_table("structure_checkin_files")
    op.drop_table("structure_checkin_sessions")
    op.drop_table("checkins")


def downgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)
    op.create_table(
        "checkins",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("client_checkin_id", uuid_type, nullable=False, unique=True),
        sa.Column("revision_id", uuid_type, sa.ForeignKey("revisions.id"), nullable=False),
        sa.Column(
            "working_version_id", uuid_type, sa.ForeignKey("working_versions.id"), nullable=True
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_by", uuid_type, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "structure_checkin_sessions",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("client_session_id", uuid_type, nullable=False, unique=True),
        sa.Column("checkout_group_id", uuid_type, nullable=False),
        sa.Column("root_revision_id", uuid_type, sa.ForeignKey("revisions.id"), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("decisions_json", postgresql.JSONB(), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", uuid_type, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "structure_checkin_files",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "session_id", uuid_type, sa.ForeignKey("structure_checkin_sessions.id"), nullable=False
        ),
        sa.Column("checkout_id", uuid_type, sa.ForeignKey("checkouts.id"), nullable=False),
        sa.Column("bundle_path", sa.String(1024), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("session_id", "checkout_id", name="uq_structure_checkin_file_target"),
    )
    op.create_table(
        "assembly_checkins",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("client_checkin_id", uuid_type, nullable=False, unique=True),
        sa.Column("base_main_version_id", uuid_type, sa.ForeignKey("working_versions.id")),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_by", uuid_type, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "assembly_checkin_files",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "assembly_checkin_id", uuid_type, sa.ForeignKey("assembly_checkins.id"), nullable=False
        ),
        sa.Column("logical_path", sa.String(1024), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("part_id", uuid_type, sa.ForeignKey("parts.id"), nullable=False),
        sa.Column("revision_id", uuid_type, sa.ForeignKey("revisions.id"), nullable=False),
        sa.Column(
            "working_version_id", uuid_type, sa.ForeignKey("working_versions.id"), nullable=False
        ),
        sa.Column("artifact_id", uuid_type, sa.ForeignKey("artifact_files.id"), nullable=False),
        sa.Column("reused", sa.Boolean(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "assembly_checkin_id", "logical_path", name="uq_assembly_files_logical_path"
        ),
    )
    op.create_table(
        "assembly_occurrences",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "assembly_checkin_id", uuid_type, sa.ForeignKey("assembly_checkins.id"), nullable=False
        ),
        sa.Column(
            "parent_version_id", uuid_type, sa.ForeignKey("working_versions.id"), nullable=False
        ),
        sa.Column(
            "child_version_id", uuid_type, sa.ForeignKey("working_versions.id"), nullable=False
        ),
        sa.Column("parent_occurrence_id", uuid_type),
        sa.Column("occurrence_key", sa.String(255), nullable=False),
        sa.Column("source_reference", sa.String(1024), nullable=False),
        sa.Column("reference_designator", sa.String(255)),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("transform_json", postgresql.JSONB(), nullable=False),
        sa.Column("is_suppressed", sa.Boolean(), nullable=False),
        sa.Column("is_visible", sa.Boolean(), nullable=False),
        sa.Column("properties_json", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_occurrence_id"],
            ["assembly_occurrences.id"],
            name="fk_assembly_occurrences_parent",
        ),
        sa.UniqueConstraint(
            "parent_version_id", "occurrence_key", name="uq_assembly_occurrences_parent_key"
        ),
    )
