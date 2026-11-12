"""Allow the controlled Phase 9 feature-creation operation catalog."""

from alembic import op

revision = "0042_phase9_feature_creation"
down_revision = "0041_phase9_cad_commits"
branch_labels = None
depends_on = None


OLD_OPERATIONS = (
    "operation IN ('feature.parameter.set_value', 'sketch.constraint.set_value', 'material.set')"
)
NEW_OPERATIONS = (
    "operation IN ('feature.parameter.set_value', 'sketch.constraint.set_value', "
    "'material.set', 'sketch.create', 'feature.create_pad', 'feature.create_pocket', "
    "'feature.create_hole', 'feature.create_fillet')"
)


def upgrade() -> None:
    op.drop_constraint("ck_cad_operation_plans_operation", "cad_operation_plans", type_="check")
    op.create_check_constraint(
        "ck_cad_operation_plans_operation", "cad_operation_plans", NEW_OPERATIONS
    )


def downgrade() -> None:
    op.drop_constraint("ck_cad_operation_plans_operation", "cad_operation_plans", type_="check")
    op.create_check_constraint(
        "ck_cad_operation_plans_operation", "cad_operation_plans", OLD_OPERATIONS
    )
