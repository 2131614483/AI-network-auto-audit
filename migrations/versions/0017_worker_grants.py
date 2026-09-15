"""Grant outbox worker access to the durable queue view."""
from alembic import op

revision = "0017_worker_grants"
down_revision = "0016_aiops_remediation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON ops.pending_outbox TO audit_app")
    op.execute("GRANT SELECT, UPDATE ON event.outbox TO audit_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON ops.pending_outbox FROM audit_app")
