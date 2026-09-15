"""Add the minimum durable Mission/DAG/TaskRun/AgentRun scheduler model."""

from alembic import op

revision = "0025_control_scheduler"
down_revision = "0024_external_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS control.workflow_definitions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          key text NOT NULL,
          name text NOT NULL,
          status text NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, key)
        );
        CREATE TABLE IF NOT EXISTS control.workflow_versions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          workflow_id uuid NOT NULL REFERENCES control.workflow_definitions(id),
          version text NOT NULL,
          graph_json jsonb NOT NULL,
          input_schema jsonb NOT NULL DEFAULT '{}',
          output_schema jsonb NOT NULL DEFAULT '{}',
          checksum text NOT NULL,
          status text NOT NULL DEFAULT 'published',
          created_by uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, workflow_id, version)
        );
        CREATE TABLE IF NOT EXISTS control.task_dependencies (
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          task_run_id uuid NOT NULL REFERENCES control.task_runs(id),
          depends_on_task_run_id uuid NOT NULL REFERENCES control.task_runs(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY(task_run_id, depends_on_task_run_id),
          CONSTRAINT task_dependencies_not_self CHECK(task_run_id <> depends_on_task_run_id)
        );
        CREATE TABLE IF NOT EXISTS control.agent_runs (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          project_id uuid REFERENCES iam.projects(id),
          task_run_id uuid NOT NULL REFERENCES control.task_runs(id),
          principal_id uuid,
          role_key text NOT NULL,
          model_key text NOT NULL,
          prompt_template_version text,
          status text NOT NULL DEFAULT 'pending',
          context_snapshot_artifact_id uuid,
          token_input integer NOT NULL DEFAULT 0,
          token_output integer NOT NULL DEFAULT 0,
          cost numeric NOT NULL DEFAULT 0,
          idempotency_key text NOT NULL,
          output_payload jsonb NOT NULL DEFAULT '{}',
          error_code text,
          error_detail text,
          started_at timestamptz,
          finished_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, idempotency_key)
        );

        ALTER TABLE control.workflow_runs
          ADD COLUMN IF NOT EXISTS project_id uuid REFERENCES iam.projects(id),
          ADD COLUMN IF NOT EXISTS workflow_version_id uuid REFERENCES control.workflow_versions(id),
          ADD COLUMN IF NOT EXISTS input_payload jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN IF NOT EXISTS output_payload jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN IF NOT EXISTS current_revision integer NOT NULL DEFAULT 1,
          ADD COLUMN IF NOT EXISTS trace_id text;
        ALTER TABLE control.task_runs
          ADD COLUMN IF NOT EXISTS project_id uuid REFERENCES iam.projects(id),
          ADD COLUMN IF NOT EXISTS plugin_version_id uuid REFERENCES catalog.plugin_versions(id),
          ADD COLUMN IF NOT EXISTS priority integer NOT NULL DEFAULT 100,
          ADD COLUMN IF NOT EXISTS input_payload jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN IF NOT EXISTS output_payload jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN IF NOT EXISTS scheduled_at timestamptz,
          ADD COLUMN IF NOT EXISTS started_at timestamptz,
          ADD COLUMN IF NOT EXISTS finished_at timestamptz,
          ADD COLUMN IF NOT EXISTS error_code text,
          ADD COLUMN IF NOT EXISTS error_detail text,
          ADD COLUMN IF NOT EXISTS trace_id text;
        """
    )
    for table in ("workflow_definitions", "workflow_versions", "task_dependencies", "agent_runs"):
        op.execute(f"ALTER TABLE control.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE control.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON control.{table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON control.{table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON control.{table} TO audit_app")
    for table, column, values in (
        ("workflow_definitions", "status", "('active','disabled','archived')"),
        ("workflow_versions", "status", "('draft','published','retired')"),
        ("agent_runs", "status", "('pending','running','completed','failed','cancelled')"),
    ):
        op.execute(
            f"ALTER TABLE control.{table} ADD CONSTRAINT {table}_{column}_check "
            f"CHECK ({column} IN {values}) NOT VALID"
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS task_dependencies_lookup_idx "
        "ON control.task_dependencies(tenant_id, task_run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS agent_runs_task_status_idx "
        "ON control.agent_runs(tenant_id, task_run_id, status)"
    )


def downgrade() -> None:
    raise RuntimeError("Scheduler execution history is not cascade-dropped; archive explicitly.")

