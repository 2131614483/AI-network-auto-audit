"""Add tenant-safe full-text and vector retrieval indexes."""

from alembic import op

revision = "0027_knowledge_retrieval"
down_revision = "0026_library_extractors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE semantic.chunks
          ADD COLUMN IF NOT EXISTS search_vector tsvector
          GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED;
        CREATE INDEX IF NOT EXISTS chunks_search_vector_gin
          ON semantic.chunks USING gin (search_vector);

        ALTER TABLE semantic.chunk_embeddings
          ADD COLUMN IF NOT EXISTS tenant_id uuid REFERENCES iam.tenants(id);
        UPDATE semantic.chunk_embeddings embedding
          SET tenant_id=chunk.tenant_id
          FROM semantic.chunks chunk
          WHERE chunk.id=embedding.chunk_id AND embedding.tenant_id IS NULL;
        ALTER TABLE semantic.chunk_embeddings ALTER COLUMN tenant_id SET NOT NULL;
        CREATE INDEX IF NOT EXISTS chunk_embeddings_tenant_idx
          ON semantic.chunk_embeddings (tenant_id, model_key);
        CREATE INDEX IF NOT EXISTS chunk_embeddings_hnsw_cosine
          ON semantic.chunk_embeddings USING hnsw (embedding vector_cosine_ops);

        ALTER TABLE semantic.chunk_embeddings ENABLE ROW LEVEL SECURITY;
        ALTER TABLE semantic.chunk_embeddings FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS chunk_embeddings_tenant_isolation ON semantic.chunk_embeddings;
        CREATE POLICY chunk_embeddings_tenant_isolation ON semantic.chunk_embeddings
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        GRANT SELECT, INSERT, UPDATE ON semantic.chunk_embeddings TO audit_app;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Knowledge retrieval indexes and embedding lineage are retained.")
