"""Add durable policy approvals, authorization leases and idempotency records."""

from alembic import op

revision = "0018_policy_control"
down_revision = "0017_worker_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS control.approvals (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          tool_call_id uuid NOT NULL REFERENCES policy.tool_calls(id),
          capability text NOT NULL,
          argument_hash text NOT NULL,
          risk_class text NOT NULL,
          status text NOT NULL DEFAULT 'pending',
          requested_by uuid,
          decided_by uuid,
          reason text,
          expires_at timestamptz NOT NULL DEFAULT (now() + interval '1 hour'),
          created_at timestamptz NOT NULL DEFAULT now(),
          decided_at timestamptz,
          UNIQUE (tenant_id, tool_call_id)
        );
        CREATE TABLE IF NOT EXISTS control.authorization_leases (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          approval_id uuid NOT NULL REFERENCES control.approvals(id),
          tool_call_id uuid NOT NULL REFERENCES policy.tool_calls(id),
          capability text NOT NULL,
          argument_hash text NOT NULL,
          expires_at timestamptz NOT NULL,
          consumed_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, approval_id)
        );
        CREATE TABLE IF NOT EXISTS control.idempotency_records (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          idempotency_key text NOT NULL,
          request_hash text NOT NULL,
          response_status integer,
          response_json jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL DEFAULT (now() + interval '24 hours'),
          UNIQUE (tenant_id, idempotency_key)
        );
        """
    )
    for table in ("approvals", "authorization_leases", "idempotency_records"):
        op.execute(f"ALTER TABLE control.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE control.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON control.{table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON control.{table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON control.{table} TO audit_app")
    op.execute(
        "CREATE INDEX IF NOT EXISTS approvals_pending_idx ON control.approvals "
        "(tenant_id, created_at) WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS authorization_leases_active_idx ON control.authorization_leases "
        "(tenant_id, expires_at) WHERE consumed_at IS NULL"
    )


def downgrade() -> None:
    # Approval and authorization history is security evidence; it must be
    # archived explicitly and is never cascade-dropped by an app downgrade.
    raise RuntimeError("Policy approval history is not cascade-dropped; archive explicitly.")

