"""Add resumable staging for structure check-in files."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0046_structure_staging"
down_revision = "0045_checkout_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "structure_checkin_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_session_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("checkout_group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "root_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("decisions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('UPLOADING', 'READY', 'COMMITTING', 'COMPLETED', 'FAILED', "
            "'CANCELLED', 'EXPIRED')",
            name="ck_structure_checkin_sessions_state",
        ),
    )
    op.create_index(
        "ix_structure_checkin_sessions_group", "structure_checkin_sessions", ["checkout_group_id"]
    )
    op.create_index(
        "ix_structure_checkin_sessions_root", "structure_checkin_sessions", ["root_revision_id"]
    )
    op.create_index("ix_structure_checkin_sessions_state", "structure_checkin_sessions", ["state"])
    op.create_index(
        "ix_structure_checkin_sessions_expires", "structure_checkin_sessions", ["expires_at"]
    )

    op.create_table(
        "structure_checkin_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("structure_checkin_sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "checkout_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("checkouts.id"),
            nullable=False,
        ),
        sa.Column("bundle_path", sa.String(length=1024), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False, unique=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('PENDING', 'UPLOADED', 'VERIFIED', 'CANCELLED')",
            name="ck_structure_checkin_files_state",
        ),
        sa.UniqueConstraint("session_id", "checkout_id", name="uq_structure_checkin_file_target"),
    )
    op.create_index("ix_structure_checkin_files_session", "structure_checkin_files", ["session_id"])
    op.create_index(
        "ix_structure_checkin_files_checkout", "structure_checkin_files", ["checkout_id"]
    )
    op.create_index("ix_structure_checkin_files_sha", "structure_checkin_files", ["sha256"])
    op.create_index("ix_structure_checkin_files_state", "structure_checkin_files", ["state"])


def downgrade() -> None:
    op.drop_table("structure_checkin_files")
    op.drop_table("structure_checkin_sessions")
