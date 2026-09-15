"""Add bounded cross-graph routing tables."""
from alembic import op

revision = "0011_graph_features"
down_revision = "0010_phase1_boundary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS graph.bridge_edges (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      source_space_id uuid NOT NULL REFERENCES graph.spaces(id), target_space_id uuid NOT NULL REFERENCES graph.spaces(id),
      source_node_id uuid NOT NULL REFERENCES graph.nodes(id), target_node_id uuid NOT NULL REFERENCES graph.nodes(id),
      relation_type text NOT NULL, weight numeric NOT NULL DEFAULT 1, status text NOT NULL DEFAULT 'active',
      properties jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(source_node_id, target_node_id, relation_type)
    );
    CREATE TABLE IF NOT EXISTS graph.gateway_nodes (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      space_id uuid NOT NULL REFERENCES graph.spaces(id), gateway_key text NOT NULL, node_id uuid REFERENCES graph.nodes(id),
      budget_json jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'active', UNIQUE(space_id, gateway_key)
    );
    CREATE INDEX IF NOT EXISTS bridge_route_idx ON graph.bridge_edges (tenant_id, source_space_id, target_space_id, status);
    """)


def downgrade() -> None:
    raise RuntimeError("Graph routing history is not cascade-dropped.")
