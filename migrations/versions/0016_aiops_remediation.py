"""Add AIOps remediation proposal and execution state."""
from alembic import op

revision = "0016_aiops_remediation"
down_revision = "0015_quant_research"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS aiops.remediation_proposals (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      incident_id uuid NOT NULL REFERENCES aiops.incidents(id), playbook_key text NOT NULL, command_spec jsonb NOT NULL,
      risk_class text NOT NULL DEFAULT 'high', status text NOT NULL DEFAULT 'proposed', proposed_by uuid, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS aiops.executions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), proposal_id uuid NOT NULL REFERENCES aiops.remediation_proposals(id),
      mode text NOT NULL DEFAULT 'dry_run', status text NOT NULL DEFAULT 'pending', authorization_id uuid, output jsonb NOT NULL DEFAULT '{}',
      started_at timestamptz, finished_at timestamptz, rollback_of uuid REFERENCES aiops.executions(id)
    );
    """)
    for table in ("remediation_proposals", "executions"):
        op.execute(f"ALTER TABLE aiops.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE aiops.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON aiops.{table} USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON aiops.{table} TO audit_app")


def downgrade() -> None:
    raise RuntimeError("AIOps execution history is retained and not cascade-dropped.")
