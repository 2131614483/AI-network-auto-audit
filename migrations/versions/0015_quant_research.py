"""Add versioned factors, experiments and simulation results."""
from alembic import op

revision = "0015_quant_research"
down_revision = "0014_audit_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS quant.factors (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      key text NOT NULL, version text NOT NULL, definition jsonb NOT NULL, status text NOT NULL DEFAULT 'experimental',
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,key,version)
    );
    CREATE TABLE IF NOT EXISTS quant.experiments (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      name text NOT NULL, factor_ids uuid[] NOT NULL DEFAULT '{}', universe jsonb NOT NULL DEFAULT '{}',
      status text NOT NULL DEFAULT 'draft', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS quant.signals (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      experiment_id uuid REFERENCES quant.experiments(id), symbol text NOT NULL, signal_date date NOT NULL,
      score numeric NOT NULL, direction text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}', UNIQUE(experiment_id,symbol,signal_date)
    );
    CREATE TABLE IF NOT EXISTS quant.risk_snapshots (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      as_of timestamptz NOT NULL, portfolio jsonb NOT NULL, metrics jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
    );
    """)
    for table in ("factors", "experiments", "signals", "risk_snapshots"):
        op.execute(f"ALTER TABLE quant.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE quant.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON quant.{table} USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON quant.{table} TO audit_app")


def downgrade() -> None:
    raise RuntimeError("Quant research history is not cascade-dropped.")
