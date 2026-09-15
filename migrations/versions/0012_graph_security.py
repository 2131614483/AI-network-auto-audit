"""Apply tenant policy and runtime grants to graph routing tables."""
from alembic import op

revision = "0012_graph_security"
down_revision = "0011_graph_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("bridge_edges", "gateway_nodes"):
        op.execute(f"ALTER TABLE graph.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE graph.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON graph.{table} USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON graph.{table} TO audit_app")


def downgrade() -> None:
    for table in ("bridge_edges", "gateway_nodes"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON graph.{table}")
