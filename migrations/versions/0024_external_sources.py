"""Register external knowledge sources without copying or scanning them."""

from alembic import op

revision = "0024_external_sources"
down_revision = "0023_relax_inbox_outbox_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge.ingest_sources (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          project_id uuid REFERENCES iam.projects(id),
          source_type text NOT NULL DEFAULT 'folder',
          name text NOT NULL,
          root_uri text NOT NULL,
          domain_hint text,
          graph_space_id uuid REFERENCES graph.spaces(id),
          watch_enabled boolean NOT NULL DEFAULT false,
          scan_interval_seconds integer NOT NULL DEFAULT 0,
          file_rules jsonb NOT NULL DEFAULT '{}',
          manifest jsonb NOT NULL DEFAULT '{}',
          status text NOT NULL DEFAULT 'active',
          read_only boolean NOT NULL DEFAULT true,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, root_uri),
          CHECK (source_type='folder'),
          CHECK (read_only),
          CHECK (scan_interval_seconds >= 0)
        );
        CREATE INDEX IF NOT EXISTS ingest_sources_status_idx
          ON knowledge.ingest_sources (tenant_id, status);
        ALTER TABLE knowledge.ingest_sources ENABLE ROW LEVEL SECURITY;
        ALTER TABLE knowledge.ingest_sources FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS ingest_sources_tenant_isolation ON knowledge.ingest_sources;
        CREATE POLICY ingest_sources_tenant_isolation ON knowledge.ingest_sources
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        GRANT SELECT, INSERT, UPDATE ON knowledge.ingest_sources TO audit_app;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Source registrations are retained; archive explicitly.")
