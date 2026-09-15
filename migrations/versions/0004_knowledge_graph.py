"""Create artifact, semantic, graph and knowledge-factory tables."""
from alembic import op

revision = "0004_knowledge_graph"
down_revision = "0003_core_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS event.outbox (
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id uuid, topic text NOT NULL, event_type text NOT NULL,
      aggregate_id uuid, payload jsonb NOT NULL, occurred_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz
    );
    CREATE TABLE IF NOT EXISTS artifact.blobs (
      sha256 text PRIMARY KEY, byte_size bigint NOT NULL, media_type text NOT NULL, storage_uri text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS artifact.artifacts (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      blob_sha256 text NOT NULL REFERENCES artifact.blobs(sha256), artifact_type text NOT NULL, classification text NOT NULL,
      metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS semantic.documents (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      source_uri text NOT NULL, title text, status text NOT NULL DEFAULT 'staged', current_version integer NOT NULL DEFAULT 1,
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, source_uri)
    );
    CREATE TABLE IF NOT EXISTS semantic.chunks (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), document_id uuid NOT NULL REFERENCES semantic.documents(id),
      ordinal integer NOT NULL, content text NOT NULL, token_count integer, metadata jsonb NOT NULL DEFAULT '{}', UNIQUE(document_id, ordinal)
    );
    CREATE TABLE IF NOT EXISTS semantic.chunk_embeddings (
      chunk_id uuid PRIMARY KEY REFERENCES semantic.chunks(id), model_key text NOT NULL, embedding vector(1024) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS graph.spaces (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), key text NOT NULL,
      level text NOT NULL, name text NOT NULL, status text NOT NULL DEFAULT 'active', created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, key)
    );
    CREATE TABLE IF NOT EXISTS graph.nodes (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), space_id uuid NOT NULL REFERENCES graph.spaces(id),
      canonical_key text NOT NULL, node_type text NOT NULL, label text NOT NULL, properties jsonb NOT NULL DEFAULT '{}',
      valid_from timestamptz NOT NULL DEFAULT now(), valid_to timestamptz, row_version integer NOT NULL DEFAULT 1, deleted_at timestamptz,
      UNIQUE(space_id, canonical_key)
    );
    CREATE TABLE IF NOT EXISTS graph.edges (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), space_id uuid NOT NULL REFERENCES graph.spaces(id),
      source_node_id uuid NOT NULL REFERENCES graph.nodes(id), target_node_id uuid NOT NULL REFERENCES graph.nodes(id), relation_type text NOT NULL,
      weight numeric NOT NULL DEFAULT 1, properties jsonb NOT NULL DEFAULT '{}', valid_from timestamptz NOT NULL DEFAULT now(), valid_to timestamptz,
      UNIQUE(space_id, source_node_id, target_node_id, relation_type)
    );
    CREATE TABLE IF NOT EXISTS knowledge.ingest_batches (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), source_uri text NOT NULL,
      status text NOT NULL DEFAULT 'staged', idempotency_key text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, idempotency_key)
    );
    CREATE TABLE IF NOT EXISTS knowledge.change_sets (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), batch_id uuid REFERENCES knowledge.ingest_batches(id),
      status text NOT NULL DEFAULT 'draft', operations jsonb NOT NULL DEFAULT '[]', proposed_by uuid, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS knowledge.releases (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), version text NOT NULL,
      status text NOT NULL DEFAULT 'draft', change_set_ids uuid[] NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, version)
    );
    CREATE TABLE IF NOT EXISTS knowledge.recycle_bin (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), entity_kind text NOT NULL,
      entity_id uuid NOT NULL, snapshot jsonb NOT NULL, deleted_by uuid, deleted_at timestamptz NOT NULL DEFAULT now(), restored_at timestamptz
    );
    CREATE INDEX IF NOT EXISTS chunks_tenant_idx ON semantic.chunks (tenant_id, document_id);
    CREATE INDEX IF NOT EXISTS nodes_space_type_idx ON graph.nodes (space_id, node_type) WHERE deleted_at IS NULL;
    CREATE INDEX IF NOT EXISTS edges_source_idx ON graph.edges (space_id, source_node_id);
    """)


def downgrade() -> None:
    raise RuntimeError("Knowledge and evidence tables are not cascade-dropped; archive explicitly.")
