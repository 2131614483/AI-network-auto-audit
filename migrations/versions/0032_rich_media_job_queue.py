"""Add a durable, single-active-job queue for local rich-media extraction."""

from alembic import op

revision = "0032_rich_media_job_queue"
down_revision = "0031_knowledge_upload_idem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge.rich_media_jobs (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          ingest_file_id uuid NOT NULL REFERENCES knowledge.ingest_files(id),
          idempotency_key text NOT NULL,
          request_hash text NOT NULL,
          method text NOT NULL CHECK (method IN ('auto','txt','ocr')),
          trace_id uuid NOT NULL,
          status text NOT NULL CHECK (status IN ('queued','processing','completed','failed')),
          attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
          lease_token uuid,
          lease_expires_at timestamptz,
          adapter_key text,
          adapter_version text,
          document_id uuid REFERENCES semantic.documents(id),
          chunk_count integer,
          page_count integer,
          duration_ms integer,
          error_code text,
          error_detail text,
          started_at timestamptz,
          finished_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id,idempotency_key)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS rich_media_jobs_one_active_per_file_uidx
          ON knowledge.rich_media_jobs (tenant_id,ingest_file_id)
          WHERE status IN ('queued','processing');
        CREATE INDEX IF NOT EXISTS rich_media_jobs_claim_idx
          ON knowledge.rich_media_jobs (tenant_id,status,created_at)
          WHERE status IN ('queued','processing');
        ALTER TABLE knowledge.rich_media_jobs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE knowledge.rich_media_jobs FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS rich_media_jobs_tenant_isolation ON knowledge.rich_media_jobs;
        CREATE POLICY rich_media_jobs_tenant_isolation ON knowledge.rich_media_jobs
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        GRANT SELECT, INSERT, UPDATE ON knowledge.rich_media_jobs TO audit_app;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Rich-media job history is retained; archive explicitly.")
