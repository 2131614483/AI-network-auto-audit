"""Harden knowledge ingestion and graph endpoint invariants.

Phase 2-4 needs a durable per-file ingest ledger and database-enforced graph
boundaries.  The original MVP tables were deliberately small, but without
these constraints a retry could be reported as successful while leaving a
batch in ``staged`` and callers could create an edge whose endpoints belong to
another graph.  This migration is additive and keeps all historical rows.
"""

from alembic import op

revision = "0018_knowledge_graph_hardening"
down_revision = "0017_worker_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE knowledge.ingest_batches
          ADD COLUMN IF NOT EXISTS scanned_count integer NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS accepted_count integer NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS skipped_count integer NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS failed_count integer NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS started_at timestamptz,
          ADD COLUMN IF NOT EXISTS finished_at timestamptz,
          ADD COLUMN IF NOT EXISTS error_detail text;

        ALTER TABLE semantic.documents
          ADD COLUMN IF NOT EXISTS content_sha256 text,
          ADD COLUMN IF NOT EXISTS source_artifact_id uuid REFERENCES artifact.artifacts(id),
          ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

        ALTER TABLE semantic.chunks
          ADD COLUMN IF NOT EXISTS document_version integer NOT NULL DEFAULT 1;
        ALTER TABLE semantic.chunks DROP CONSTRAINT IF EXISTS chunks_document_id_ordinal_key;
        CREATE UNIQUE INDEX IF NOT EXISTS chunks_document_version_ordinal_uidx
          ON semantic.chunks (document_id, document_version, ordinal);

        CREATE TABLE IF NOT EXISTS knowledge.ingest_files (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          batch_id uuid NOT NULL REFERENCES knowledge.ingest_batches(id),
          relative_path text NOT NULL,
          source_uri text NOT NULL,
          sha256 text NOT NULL,
          size_bytes bigint NOT NULL,
          mime_type text NOT NULL,
          status text NOT NULL DEFAULT 'staged',
          source_artifact_id uuid REFERENCES artifact.artifacts(id),
          document_id uuid REFERENCES semantic.documents(id),
          error_code text,
          error_detail text,
          created_at timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz,
          UNIQUE(batch_id, relative_path),
          UNIQUE(tenant_id, source_uri, sha256)
        );
        CREATE INDEX IF NOT EXISTS ingest_files_batch_status_idx
          ON knowledge.ingest_files (tenant_id, batch_id, status);
        CREATE INDEX IF NOT EXISTS ingest_files_source_idx
          ON knowledge.ingest_files (tenant_id, source_uri, sha256);

        ALTER TABLE graph.edges
          ADD COLUMN IF NOT EXISTS deleted_at timestamptz,
          ADD COLUMN IF NOT EXISTS row_version integer NOT NULL DEFAULT 1;
        CREATE INDEX IF NOT EXISTS graph_edges_active_out_idx
          ON graph.edges (tenant_id, space_id, source_node_id, relation_type)
          WHERE valid_to IS NULL AND deleted_at IS NULL;
        CREATE INDEX IF NOT EXISTS graph_edges_active_in_idx
          ON graph.edges (tenant_id, space_id, target_node_id, relation_type)
          WHERE valid_to IS NULL AND deleted_at IS NULL;

        CREATE OR REPLACE FUNCTION graph.validate_internal_edge()
        RETURNS trigger LANGUAGE plpgsql AS $fn$
        DECLARE source_space uuid; target_space uuid; source_tenant uuid; target_tenant uuid;
        BEGIN
          SELECT space_id, tenant_id INTO source_space, source_tenant
            FROM graph.nodes WHERE id = NEW.source_node_id;
          SELECT space_id, tenant_id INTO target_space, target_tenant
            FROM graph.nodes WHERE id = NEW.target_node_id;
          IF source_space IS NULL OR target_space IS NULL THEN
            RAISE EXCEPTION 'graph edge endpoint does not exist';
          END IF;
          IF source_space <> NEW.space_id OR target_space <> NEW.space_id THEN
            RAISE EXCEPTION 'graph edge endpoints must belong to edge space';
          END IF;
          IF source_tenant <> NEW.tenant_id OR target_tenant <> NEW.tenant_id THEN
            RAISE EXCEPTION 'graph edge endpoint tenant mismatch';
          END IF;
          RETURN NEW;
        END;
        $fn$;
        DROP TRIGGER IF EXISTS graph_edges_same_space ON graph.edges;
        CREATE CONSTRAINT TRIGGER graph_edges_same_space
          AFTER INSERT OR UPDATE OF space_id, source_node_id, target_node_id, tenant_id
          ON graph.edges DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION graph.validate_internal_edge();

        CREATE OR REPLACE FUNCTION graph.validate_bridge_edge()
        RETURNS trigger LANGUAGE plpgsql AS $fn$
        DECLARE source_space uuid; target_space uuid; source_tenant uuid; target_tenant uuid;
        BEGIN
          SELECT space_id, tenant_id INTO source_space, source_tenant
            FROM graph.nodes WHERE id = NEW.source_node_id;
          SELECT space_id, tenant_id INTO target_space, target_tenant
            FROM graph.nodes WHERE id = NEW.target_node_id;
          IF source_space IS NULL OR target_space IS NULL THEN
            RAISE EXCEPTION 'bridge endpoint does not exist';
          END IF;
          IF NEW.source_space_id = NEW.target_space_id THEN
            RAISE EXCEPTION 'bridge edge must connect different graph spaces';
          END IF;
          IF source_space <> NEW.source_space_id OR target_space <> NEW.target_space_id THEN
            RAISE EXCEPTION 'bridge endpoint space mismatch';
          END IF;
          IF source_tenant <> NEW.tenant_id OR target_tenant <> NEW.tenant_id THEN
            RAISE EXCEPTION 'bridge endpoint tenant mismatch';
          END IF;
          RETURN NEW;
        END;
        $fn$;
        DROP TRIGGER IF EXISTS bridge_edges_validated ON graph.bridge_edges;
        CREATE CONSTRAINT TRIGGER bridge_edges_validated
          AFTER INSERT OR UPDATE OF source_space_id, target_space_id, source_node_id, target_node_id, tenant_id
          ON graph.bridge_edges DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION graph.validate_bridge_edge();

        ALTER TABLE knowledge.ingest_files ENABLE ROW LEVEL SECURITY;
        ALTER TABLE knowledge.ingest_files FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS ingest_files_tenant_isolation ON knowledge.ingest_files;
        CREATE POLICY ingest_files_tenant_isolation ON knowledge.ingest_files
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        GRANT SELECT, INSERT, UPDATE ON knowledge.ingest_files TO audit_app;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Knowledge and graph hardening is retained; archive explicitly.")
