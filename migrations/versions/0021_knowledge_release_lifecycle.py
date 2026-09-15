"""Add durable knowledge ChangeSet validation and release lifecycle metadata."""

from alembic import op

revision = "0021_knowledge_release_lifecycle"
down_revision = "0020_audit_evidence_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE knowledge.change_sets
          ADD COLUMN IF NOT EXISTS project_id uuid REFERENCES iam.projects(id),
          ADD COLUMN IF NOT EXISTS graph_space_id uuid REFERENCES graph.spaces(id),
          ADD COLUMN IF NOT EXISTS parent_release_id uuid REFERENCES knowledge.releases(id),
          ADD COLUMN IF NOT EXISTS change_type text NOT NULL DEFAULT 'graph',
          ADD COLUMN IF NOT EXISTS title text NOT NULL DEFAULT 'Knowledge change',
          ADD COLUMN IF NOT EXISTS reason text NOT NULL DEFAULT '',
          ADD COLUMN IF NOT EXISTS actor_id uuid,
          ADD COLUMN IF NOT EXISTS source_batch_id uuid REFERENCES knowledge.ingest_batches(id),
          ADD COLUMN IF NOT EXISTS risk_class text NOT NULL DEFAULT 'low',
          ADD COLUMN IF NOT EXISTS checksum text,
          ADD COLUMN IF NOT EXISTS submitted_at timestamptz,
          ADD COLUMN IF NOT EXISTS approved_at timestamptz,
          ADD COLUMN IF NOT EXISTS applied_at timestamptz,
          ADD COLUMN IF NOT EXISTS rolled_back_from_id uuid REFERENCES knowledge.change_sets(id);

        ALTER TABLE knowledge.change_operations
          ADD COLUMN IF NOT EXISTS operation_order integer NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS object_type text,
          ADD COLUMN IF NOT EXISTS object_id uuid,
          ADD COLUMN IF NOT EXISTS operation text,
          ADD COLUMN IF NOT EXISTS before_json jsonb,
          ADD COLUMN IF NOT EXISTS after_json jsonb,
          ADD COLUMN IF NOT EXISTS inverse_json jsonb,
          ADD COLUMN IF NOT EXISTS source_claim_ids uuid[];
        CREATE INDEX IF NOT EXISTS change_operations_order_idx
          ON knowledge.change_operations (tenant_id, change_set_id, operation_order);

        ALTER TABLE knowledge.releases
          ADD COLUMN IF NOT EXISTS project_id uuid REFERENCES iam.projects(id),
          ADD COLUMN IF NOT EXISTS parent_release_id uuid REFERENCES knowledge.releases(id),
          ADD COLUMN IF NOT EXISTS changeset_id uuid REFERENCES knowledge.change_sets(id),
          ADD COLUMN IF NOT EXISTS chunk_release_key text,
          ADD COLUMN IF NOT EXISTS embedding_release_key text,
          ADD COLUMN IF NOT EXISTS graph_release_key text,
          ADD COLUMN IF NOT EXISTS checksum text,
          ADD COLUMN IF NOT EXISTS activated_at timestamptz,
          ADD COLUMN IF NOT EXISTS deactivated_at timestamptz;
        CREATE UNIQUE INDEX IF NOT EXISTS releases_one_active_per_project_uidx
          ON knowledge.releases (tenant_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid))
          WHERE status='active';

        CREATE TABLE IF NOT EXISTS knowledge.validation_runs (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          change_set_id uuid NOT NULL REFERENCES knowledge.change_sets(id),
          validator_key text NOT NULL,
          validator_version text NOT NULL,
          status text NOT NULL DEFAULT 'running',
          metrics jsonb NOT NULL DEFAULT '{}',
          violations jsonb NOT NULL DEFAULT '[]',
          started_at timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz
        );
        ALTER TABLE knowledge.validation_runs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE knowledge.validation_runs FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS validation_runs_tenant_isolation ON knowledge.validation_runs;
        CREATE POLICY validation_runs_tenant_isolation ON knowledge.validation_runs
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        GRANT SELECT, INSERT, UPDATE ON knowledge.validation_runs TO audit_app;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Knowledge release history is retained; archive explicitly.")
