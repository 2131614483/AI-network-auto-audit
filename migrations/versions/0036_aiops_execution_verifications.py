"""Retain human verification evidence for local AIOps Canary simulations."""

from alembic import op

revision = "0036_aiops_exec_verification"
down_revision = "0035_node_revisions_merged_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS aiops.execution_verifications (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          execution_id uuid NOT NULL REFERENCES aiops.executions(id),
          outcome text NOT NULL CHECK (outcome IN ('healthy', 'rollback')),
          reviewer_label text NOT NULL CHECK (char_length(btrim(reviewer_label)) >= 2),
          trace_id uuid NOT NULL,
          idempotency_key text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, execution_id)
        );
        CREATE INDEX IF NOT EXISTS aiops_execution_verifications_execution_idx
          ON aiops.execution_verifications(tenant_id, execution_id, created_at DESC);
        """
    )
    op.execute("ALTER TABLE aiops.execution_verifications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE aiops.execution_verifications FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY execution_verifications_tenant_isolation ON aiops.execution_verifications "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT ON aiops.execution_verifications TO audit_app")


def downgrade() -> None:
    raise RuntimeError("AIOps verification evidence is retained and not hard-deleted.")
