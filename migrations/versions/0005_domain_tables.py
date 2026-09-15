"""Create belief, audit, quant, AIOps, risk and operations tables."""
from alembic import op

revision = "0005_domain_tables"
down_revision = "0004_knowledge_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS belief.claims (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), subject text NOT NULL,
      predicate text NOT NULL, object text NOT NULL, confidence numeric NOT NULL DEFAULT 0, status text NOT NULL DEFAULT 'proposed', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS audit.engagements (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), project_id uuid NOT NULL REFERENCES iam.projects(id),
      name text NOT NULL, period_start date, period_end date, status text NOT NULL DEFAULT 'planned', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS audit.findings (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), engagement_id uuid NOT NULL REFERENCES audit.engagements(id),
      title text NOT NULL, severity text NOT NULL, status text NOT NULL DEFAULT 'open', evidence_artifact_ids uuid[] NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS quant.datasets (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), key text NOT NULL,
      as_of timestamptz, freshness_status text NOT NULL DEFAULT 'unknown', metadata jsonb NOT NULL DEFAULT '{}', UNIQUE(tenant_id, key)
    );
    CREATE TABLE IF NOT EXISTS quant.backtests (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), dataset_id uuid REFERENCES quant.datasets(id),
      strategy_key text NOT NULL, parameters jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'queued', metrics jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS aiops.alerts (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), source text NOT NULL,
      fingerprint text NOT NULL, severity text NOT NULL, payload jsonb NOT NULL, occurred_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, fingerprint)
    );
    CREATE TABLE IF NOT EXISTS aiops.incidents (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), title text NOT NULL,
      status text NOT NULL DEFAULT 'open', root_cause jsonb, timeline jsonb NOT NULL DEFAULT '[]', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS risk.events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id), domain text NOT NULL,
      risk_class text NOT NULL, score numeric NOT NULL DEFAULT 0, status text NOT NULL DEFAULT 'open', payload jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS ops.audit_log (
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id uuid, actor_id uuid, action text NOT NULL,
      resource_type text, resource_id uuid, payload jsonb NOT NULL DEFAULT '{}', trace_id text, created_at timestamptz NOT NULL DEFAULT now()
    );
    """)


def downgrade() -> None:
    raise RuntimeError("Domain evidence tables are not cascade-dropped; archive explicitly.")
