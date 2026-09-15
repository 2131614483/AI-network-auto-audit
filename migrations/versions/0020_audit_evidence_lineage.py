"""Add auditable evidence, workpaper and finding-to-claim lineage."""

from alembic import op

revision = "0020_audit_evidence_lineage"
down_revision = "0019_merge_hardening_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit.workpapers (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          engagement_id uuid NOT NULL REFERENCES audit.engagements(id),
          workpaper_key text NOT NULL,
          status text NOT NULL DEFAULT 'draft',
          content jsonb NOT NULL DEFAULT '{}',
          artifact_ids uuid[] NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(engagement_id, workpaper_key)
        );
        CREATE TABLE IF NOT EXISTS belief.evidence_links (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          claim_id uuid NOT NULL REFERENCES belief.claims(id),
          artifact_id uuid NOT NULL REFERENCES artifact.artifacts(id),
          audit_evidence_id uuid REFERENCES audit.evidence(id),
          relation text NOT NULL DEFAULT 'supports',
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(claim_id, artifact_id, audit_evidence_id, relation)
        );
        CREATE TABLE IF NOT EXISTS audit.finding_claims (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          finding_id uuid NOT NULL REFERENCES audit.findings(id),
          claim_id uuid NOT NULL REFERENCES belief.claims(id),
          audit_evidence_id uuid NOT NULL REFERENCES audit.evidence(id),
          reviewer_label text NOT NULL,
          review_status text NOT NULL DEFAULT 'confirmed',
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(finding_id, claim_id)
        );
        """
    )
    for schema, table in (("audit", "workpapers"), ("belief", "evidence_links"), ("audit", "finding_claims")):
        op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {schema}.{table} "
                   "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
                   "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {schema}.{table} TO audit_app")


def downgrade() -> None:
    raise RuntimeError("Audit evidence lineage is immutable and is not cascade-dropped.")
