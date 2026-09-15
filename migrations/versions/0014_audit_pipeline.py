"""Add audit evidence and anomaly candidate tables."""
from alembic import op

revision = "0014_audit_pipeline"
down_revision = "0013_node_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS audit.evidence (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      engagement_id uuid NOT NULL REFERENCES audit.engagements(id), artifact_id uuid REFERENCES artifact.artifacts(id),
      evidence_type text NOT NULL, description text, metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS audit.anomaly_candidates (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      engagement_id uuid NOT NULL REFERENCES audit.engagements(id), source_ref text NOT NULL, rule_key text NOT NULL,
      score numeric NOT NULL DEFAULT 0, status text NOT NULL DEFAULT 'open', payload jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(engagement_id, source_ref, rule_key)
    );
    """)
    for table in ("evidence", "anomaly_candidates"):
        op.execute(f"ALTER TABLE audit.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE audit.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON audit.{table} USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON audit.{table} TO audit_app")


def downgrade() -> None:
    raise RuntimeError("Audit evidence is retained and not cascade-dropped.")
