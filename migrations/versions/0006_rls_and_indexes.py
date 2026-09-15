"""Enable tenant isolation and durable outbox indexes."""
from alembic import op

revision = "0006_rls_and_indexes"
down_revision = "0005_domain_tables"
branch_labels = None
depends_on = None

TABLES = (
    ("iam", "projects"), ("iam", "principals"), ("policy", "policy_sets"), ("policy", "tool_calls"),
    ("policy", "decisions"), ("catalog", "plugins"), ("catalog", "plugin_versions"),
    ("catalog", "ui_contributions"), ("control", "missions"), ("control", "workflow_runs"),
    ("control", "task_runs"), ("artifact", "artifacts"), ("semantic", "documents"), ("semantic", "chunks"),
    ("graph", "spaces"), ("graph", "nodes"), ("graph", "edges"), ("knowledge", "ingest_batches"),
    ("knowledge", "change_sets"), ("knowledge", "releases"), ("knowledge", "recycle_bin"),
    ("belief", "claims"), ("audit", "engagements"), ("audit", "findings"), ("quant", "datasets"),
    ("quant", "backtests"), ("aiops", "alerts"), ("aiops", "incidents"), ("risk", "events"),
)


def upgrade() -> None:
    for schema, table in TABLES:
        qualified = f"{schema}.{table}"
        policy = f"{table}_tenant_isolation"
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {qualified}")
        op.execute(
            f"CREATE POLICY {policy} ON {qualified} USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
    op.execute("CREATE INDEX IF NOT EXISTS outbox_pending_idx ON event.outbox (occurred_at) WHERE published_at IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS tool_calls_trace_idx ON policy.tool_calls (tenant_id, trace_id)")


def downgrade() -> None:
    for schema, table in TABLES:
        op.execute(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY")
