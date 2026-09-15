"""Enable platform PostgreSQL extensions."""
from alembic import op

revision = "0001_extensions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for extension in ("pgcrypto", "pg_trgm", "ltree", "vector"):
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")


def downgrade() -> None:
    # Shared extensions are never removed by an application downgrade.
    pass
