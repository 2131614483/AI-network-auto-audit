"""Add node aliases, conflict inbox and auditable change operations."""
from alembic import op

revision = "0013_node_lifecycle"
down_revision = "0012_graph_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS graph.node_aliases (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      node_id uuid NOT NULL REFERENCES graph.nodes(id), alias text NOT NULL, alias_type text NOT NULL DEFAULT 'name',
      confidence numeric NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(node_id, alias)
    );
    CREATE TABLE IF NOT EXISTS knowledge.conflict_cases (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      entity_key text NOT NULL, status text NOT NULL DEFAULT 'open', severity text NOT NULL DEFAULT 'medium',
      summary text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz
    );
    CREATE TABLE IF NOT EXISTS knowledge.conflict_items (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      case_id uuid NOT NULL REFERENCES knowledge.conflict_cases(id), source_uri text NOT NULL, claim jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS knowledge.change_operations (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      change_set_id uuid NOT NULL REFERENCES knowledge.change_sets(id), operation_type text NOT NULL, target_kind text NOT NULL,
      target_id uuid, payload jsonb NOT NULL, status text NOT NULL DEFAULT 'proposed', created_at timestamptz NOT NULL DEFAULT now()
    );
    """)
    for schema, table in (("graph", "node_aliases"), ("knowledge", "conflict_cases"), ("knowledge", "conflict_items"), ("knowledge", "change_operations")):
        op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {schema}.{table} USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {schema}.{table} TO audit_app")


def downgrade() -> None:
    raise RuntimeError("Node lifecycle history is not cascade-dropped.")
