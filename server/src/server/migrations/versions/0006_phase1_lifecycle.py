"""Add Phase 1 working versions, checkout locks, and terminal check-in states."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_phase1_lifecycle"
down_revision = "0005_search_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "working_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("source_sha256", sa.String(64)),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_in_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("revision_id", "version_number", name="uq_working_versions_number"),
        sa.CheckConstraint(
            "state IN ('DRAFT', 'UPLOADING', 'VALIDATING', 'COMMITTED', 'PROCESSING', "
            "'READY', 'READY_WITH_WARNINGS', 'FAILED', 'CANCELLED', 'EXPIRED')",
            name="ck_working_versions_state",
        ),
    )
    op.create_index("ix_working_versions_revision_id", "working_versions", ["revision_id"])
    op.create_index("ix_working_versions_source_sha256", "working_versions", ["source_sha256"])
    op.create_index("ix_working_versions_state", "working_versions", ["state"])

    op.add_column(
        "revisions",
        sa.Column("current_working_version_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column("artifacts", sa.Column("working_version_id", postgresql.UUID(as_uuid=True)))
    op.add_column("checkins", sa.Column("working_version_id", postgresql.UUID(as_uuid=True)))

    op.execute(
        """
        INSERT INTO working_versions
            (id, revision_id, version_number, state, source_sha256, metadata_json, created_by,
             created_at, checked_in_at)
        SELECT gen_random_uuid(), r.id, 1, r.state,
               (SELECT a.sha256 FROM artifacts a
                WHERE a.revision_id = r.id AND a.kind = 'NATIVE'
                ORDER BY a.created_at LIMIT 1),
               r.metadata_json, r.created_by, r.created_at, r.committed_at
        FROM revisions r
        WHERE r.state <> 'DRAFT'
           OR EXISTS (SELECT 1 FROM artifacts a WHERE a.revision_id = r.id)
        """
    )
    op.execute(
        """
        UPDATE revisions r SET current_working_version_id = w.id
        FROM working_versions w WHERE w.revision_id = r.id
        """
    )
    op.execute(
        """
        UPDATE artifacts a SET working_version_id = w.id
        FROM working_versions w WHERE w.revision_id = a.revision_id
        """
    )
    op.execute(
        """
        UPDATE checkins c SET working_version_id = w.id
        FROM working_versions w WHERE w.revision_id = c.revision_id
        """
    )

    op.alter_column("artifacts", "working_version_id", nullable=False)
    op.alter_column("checkins", "working_version_id", nullable=False)
    op.create_index("ix_artifacts_working_version_id", "artifacts", ["working_version_id"])
    op.drop_constraint("checkins_revision_id_key", "checkins", type_="unique")
    op.create_index("ix_checkins_revision_id", "checkins", ["revision_id"])
    op.create_index("ix_checkins_working_version_id", "checkins", ["working_version_id"])
    op.create_foreign_key(
        "fk_revisions_current_working_version",
        "revisions",
        "working_versions",
        ["current_working_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_artifacts_working_version",
        "artifacts",
        "working_versions",
        ["working_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_checkins_working_version",
        "checkins",
        "working_versions",
        ["working_version_id"],
        ["id"],
    )

    op.add_column("processing_jobs", sa.Column("working_version_id", postgresql.UUID(as_uuid=True)))
    op.execute(
        """
        UPDATE processing_jobs j SET working_version_id = w.id
        FROM working_versions w WHERE w.revision_id = j.revision_id
        """
    )
    op.create_index(
        "ix_processing_jobs_working_version_id", "processing_jobs", ["working_version_id"]
    )
    op.create_foreign_key(
        "fk_processing_jobs_working_version",
        "processing_jobs",
        "working_versions",
        ["working_version_id"],
        ["id"],
    )

    op.drop_constraint("ck_checkins_state", "checkins", type_="check")
    op.create_check_constraint(
        "ck_checkins_state",
        "checkins",
        "state IN ('UPLOADING', 'VALIDATING', 'COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED')",
    )

    op.create_table(
        "checkouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "part_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "base_working_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("working_versions.id"),
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_checkouts_part_id", "checkouts", ["part_id"], unique=True)
    op.create_index("ix_checkouts_expires_at", "checkouts", ["expires_at"])


def downgrade() -> None:
    op.drop_table("checkouts")
    op.drop_constraint("ck_checkins_state", "checkins", type_="check")
    op.create_check_constraint(
        "ck_checkins_state",
        "checkins",
        "state IN ('UPLOADING', 'VALIDATING', 'COMPLETED', 'FAILED')",
    )
    op.drop_constraint("fk_checkins_working_version", "checkins", type_="foreignkey")
    op.drop_constraint("fk_artifacts_working_version", "artifacts", type_="foreignkey")
    op.drop_constraint("fk_processing_jobs_working_version", "processing_jobs", type_="foreignkey")
    op.drop_index("ix_processing_jobs_working_version_id", table_name="processing_jobs")
    op.drop_column("processing_jobs", "working_version_id")
    op.drop_constraint("fk_revisions_current_working_version", "revisions", type_="foreignkey")
    op.drop_index("ix_checkins_working_version_id", table_name="checkins")
    op.drop_index("ix_checkins_revision_id", table_name="checkins")
    op.create_unique_constraint("checkins_revision_id_key", "checkins", ["revision_id"])
    op.drop_index("ix_artifacts_working_version_id", table_name="artifacts")
    op.drop_column("checkins", "working_version_id")
    op.drop_column("artifacts", "working_version_id")
    op.drop_column("revisions", "current_working_version_id")
    op.drop_table("working_versions")
