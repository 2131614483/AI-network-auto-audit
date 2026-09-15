"""Persist replayable business results for tenant-scoped knowledge uploads."""

from alembic import op

revision = "0031_knowledge_upload_idem"
down_revision = "0030_graph_visualization_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge.upload_idempotency (
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          idempotency_key text NOT NULL,
          request_hash text NOT NULL,
          status text NOT NULL CHECK (status IN ('processing','completed','failed')),
          response_json jsonb,
          failure_detail text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id,idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS knowledge_upload_idempotency_status_idx
          ON knowledge.upload_idempotency(tenant_id,status,updated_at);
        ALTER TABLE knowledge.upload_idempotency ENABLE ROW LEVEL SECURITY;
        ALTER TABLE knowledge.upload_idempotency FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS upload_idempotency_tenant_isolation ON knowledge.upload_idempotency;
        CREATE POLICY upload_idempotency_tenant_isolation ON knowledge.upload_idempotency
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        GRANT SELECT, INSERT, UPDATE ON knowledge.upload_idempotency TO audit_app;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Knowledge upload replay records are retained; archive explicitly.")
