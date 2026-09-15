"""Create IAM, policy, plugin and workflow control-plane tables."""
from alembic import op

revision = "0003_core_control"
down_revision = "0002_schemas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS iam.tenants (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), slug text NOT NULL UNIQUE,
      name text NOT NULL, status text NOT NULL DEFAULT 'active', settings jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now()
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS iam.projects (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      slug text NOT NULL, name text NOT NULL, status text NOT NULL DEFAULT 'active', settings jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, slug)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS iam.principals (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      kind text NOT NULL, subject text NOT NULL, display_name text, status text NOT NULL DEFAULT 'active',
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, subject)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS policy.policy_sets (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      name text NOT NULL, version integer NOT NULL DEFAULT 1, status text NOT NULL DEFAULT 'draft',
      rules jsonb NOT NULL DEFAULT '[]', created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, name, version)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS policy.tool_calls (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      principal_id uuid, capability text NOT NULL, arguments jsonb NOT NULL DEFAULT '{}',
      argument_hash text NOT NULL, requested_at timestamptz NOT NULL DEFAULT now(), trace_id text
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS policy.decisions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      tool_call_id uuid NOT NULL REFERENCES policy.tool_calls(id), decision text NOT NULL,
      risk_score numeric NOT NULL DEFAULT 0, reason text NOT NULL, matched_rule_ids uuid[] NOT NULL DEFAULT '{}',
      decided_at timestamptz NOT NULL DEFAULT now()
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS catalog.plugins (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      key text NOT NULL, name text NOT NULL, category text NOT NULL, status text NOT NULL DEFAULT 'active',
      description text, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, key)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS catalog.plugin_versions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      plugin_id uuid NOT NULL REFERENCES catalog.plugins(id), version text NOT NULL, manifest_json jsonb NOT NULL,
      package_uri text, package_sha256 text, signature text, runtime text NOT NULL, entrypoint text NOT NULL,
      side_effect_class text NOT NULL DEFAULT 'none', status text NOT NULL DEFAULT 'draft', published_at timestamptz,
      UNIQUE(tenant_id, plugin_id, version)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS catalog.ui_contributions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      plugin_version_id uuid NOT NULL REFERENCES catalog.plugin_versions(id), contribution_key text NOT NULL,
      schema_version text NOT NULL, contribution_type text NOT NULL, slot text NOT NULL, renderer text,
      data_contract jsonb, action_contract jsonb, config_json jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'active',
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, plugin_version_id, contribution_key)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS control.missions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      project_id uuid NOT NULL REFERENCES iam.projects(id), domain text NOT NULL, title text NOT NULL, objective text NOT NULL,
      acceptance_criteria jsonb NOT NULL DEFAULT '[]', autonomy_mode text NOT NULL DEFAULT 'approval_required',
      status text NOT NULL DEFAULT 'planned', requested_by uuid, created_at timestamptz NOT NULL DEFAULT now()
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS control.workflow_runs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      mission_id uuid REFERENCES control.missions(id), workflow_key text NOT NULL, workflow_version text NOT NULL,
      status text NOT NULL DEFAULT 'pending', idempotency_key text NOT NULL, started_at timestamptz, finished_at timestamptz,
      UNIQUE(tenant_id, idempotency_key)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS control.task_runs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
      workflow_run_id uuid NOT NULL REFERENCES control.workflow_runs(id), node_key text NOT NULL, capability text NOT NULL,
      status text NOT NULL DEFAULT 'pending', attempt integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 1,
      idempotency_key text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, idempotency_key)
    )
    """)


def downgrade() -> None:
    raise RuntimeError("Control-plane tables are not cascade-dropped; archive explicitly.")
