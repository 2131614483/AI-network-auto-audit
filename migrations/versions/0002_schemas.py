"""Create isolated domain schemas."""
from alembic import op

revision = "0002_schemas"
down_revision = "0001_extensions"
branch_labels = None
depends_on = None

SCHEMAS = (
    "iam", "policy", "catalog", "control", "event", "artifact", "semantic",
    "graph", "knowledge", "belief", "audit", "quant", "aiops", "risk", "ops",
)


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")


def downgrade() -> None:
    # Schemas contain evidence and are not cascade-dropped.
    pass
