"""Mark completion of the database-base boundary before feature phases."""
revision = "0010_phase1_boundary"
down_revision = "0009_functions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Boundary marker only; no destructive or privilege-changing operation.
    pass


def downgrade() -> None:
    pass
