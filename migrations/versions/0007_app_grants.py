"""Grant least-privilege runtime access to the application role."""
from alembic import op

revision = "0007_app_grants"
down_revision = "0006_rls_and_indexes"
branch_labels = None
depends_on = None

SCHEMAS = ("iam", "policy", "catalog", "control", "event", "artifact", "semantic", "graph", "knowledge", "belief", "audit", "quant", "aiops", "risk", "ops")


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO audit_app")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {schema} TO audit_app")
        op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT, INSERT, UPDATE ON TABLES TO audit_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA event, ops TO audit_app")


def downgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"REVOKE SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {schema} FROM audit_app")
