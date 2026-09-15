"""Require approved, hash-bound AIOps change requests for live execution."""

from alembic import op

revision = "0018_aiops_change_authorization"
down_revision = "0017_worker_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS aiops.change_requests (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      proposal_id uuid NOT NULL REFERENCES aiops.remediation_proposals(id),
      command_hash text NOT NULL,
      status text NOT NULL DEFAULT 'pending',
      approved_by uuid,
      approved_at timestamptz,
      expires_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(tenant_id, proposal_id, command_hash)
    );
    ALTER TABLE aiops.executions ADD COLUMN IF NOT EXISTS change_request_id uuid REFERENCES aiops.change_requests(id);
    ALTER TABLE aiops.executions ADD COLUMN IF NOT EXISTS command_hash text;
    CREATE INDEX IF NOT EXISTS aiops_change_requests_lookup_idx
      ON aiops.change_requests(tenant_id, proposal_id, status);
    """)
    op.execute("ALTER TABLE aiops.change_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE aiops.change_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY change_requests_tenant_isolation ON aiops.change_requests "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON aiops.change_requests TO audit_app")


def downgrade() -> None:
    raise RuntimeError("AIOps change authorization history is retained and not cascade-dropped.")
