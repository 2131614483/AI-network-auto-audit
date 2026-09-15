"""Phase 6: governed graph merge / split and conflict arbitration ledgers.

Merge re-points edges of collapsed source nodes onto a surviving target node
and soft-deletes sources into the recycle bin (reversible).  Split re-points a
declared subset of a source node's edges onto newly created parts.  Every merge
and split records an immutable ledger (members / parts / edge redirects) so it
can be audited or compensated.  Conflict arbitration is an explicit decision
(never auto-resolved) recorded against an open conflict case.

All tables are tenant-scoped and enforce RLS.  The merge/split source nodes are
soft-deleted (deleted_at / recycle bin + node_revisions), never hard-removed.
"""

from alembic import op

revision = "0034_graph_merge_arbitration"
down_revision = "0033_multigraph_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS graph.merge_records (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      space_id uuid NOT NULL REFERENCES graph.spaces(id),
      target_node_id uuid NOT NULL REFERENCES graph.nodes(id),
      status text NOT NULL DEFAULT 'applied' CHECK (status IN ('applied','rolled_back')),
      reason text,
      checksum text,
      created_at timestamptz NOT NULL DEFAULT now(),
      rolled_back_at timestamptz
    );
    CREATE TABLE IF NOT EXISTS graph.merge_members (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      merge_id uuid NOT NULL REFERENCES graph.merge_records(id),
      node_id uuid NOT NULL REFERENCES graph.nodes(id),
      role text NOT NULL CHECK (role IN ('source','target')),
      node_type text NOT NULL,
      label text NOT NULL,
      properties jsonb NOT NULL DEFAULT '{}',
      deleted_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS graph.merge_edge_redirects (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      merge_id uuid NOT NULL REFERENCES graph.merge_records(id),
      edge_id uuid NOT NULL REFERENCES graph.edges(id),
      from_node_id uuid NOT NULL REFERENCES graph.nodes(id),
      to_node_id uuid NOT NULL REFERENCES graph.nodes(id),
      relation_type text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS graph.split_records (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      space_id uuid NOT NULL REFERENCES graph.spaces(id),
      source_node_id uuid NOT NULL REFERENCES graph.nodes(id),
      status text NOT NULL DEFAULT 'applied' CHECK (status IN ('applied','rolled_back')),
      reason text,
      checksum text,
      created_at timestamptz NOT NULL DEFAULT now(),
      rolled_back_at timestamptz
    );
    CREATE TABLE IF NOT EXISTS graph.split_parts (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      split_id uuid NOT NULL REFERENCES graph.split_records(id),
      node_id uuid NOT NULL REFERENCES graph.nodes(id),
      node_type text NOT NULL,
      label text NOT NULL,
      properties jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS graph.split_edge_redirects (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      split_id uuid NOT NULL REFERENCES graph.split_records(id),
      edge_id uuid NOT NULL REFERENCES graph.edges(id),
      to_node_id uuid NOT NULL REFERENCES graph.nodes(id),
      relation_type text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS knowledge.arbitration_decisions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      case_id uuid NOT NULL REFERENCES knowledge.conflict_cases(id),
      decision text NOT NULL CHECK (decision IN ('merge','keep_source','reject_claim','resolve')),
      reason text,
      resolution_changeset_id uuid,
      decided_by uuid,
      decided_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX IF NOT EXISTS arbitration_decisions_case_once
      ON knowledge.arbitration_decisions (tenant_id, case_id);
    CREATE INDEX IF NOT EXISTS merge_records_space_idx ON graph.merge_records (tenant_id, space_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS split_records_space_idx ON graph.split_records (tenant_id, space_id, created_at DESC);
    """)
    _RLS = {
        "graph.merge_records": "merge_records_tenant_isolation",
        "graph.merge_members": "merge_members_tenant_isolation",
        "graph.merge_edge_redirects": "merge_edge_redirects_tenant_isolation",
        "graph.split_records": "split_records_tenant_isolation",
        "graph.split_parts": "split_parts_tenant_isolation",
        "graph.split_edge_redirects": "split_edge_redirects_tenant_isolation",
        "knowledge.arbitration_decisions": "arbitration_decisions_tenant_isolation",
    }
    for table, policy in _RLS.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"DROP POLICY IF EXISTS {policy} ON {table};\n"
            f"CREATE POLICY {policy} ON {table} "
            f"USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON graph.merge_records, graph.merge_members, graph.merge_edge_redirects, "
        "graph.split_records, graph.split_parts, graph.split_edge_redirects, knowledge.arbitration_decisions TO audit_app"
    )


def downgrade() -> None:
    raise RuntimeError("Phase 6 governance ledgers are not cascade-dropped; archive explicitly.")