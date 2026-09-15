"""Experience layer: accumulate real plugin collaboration into the nebula.

Five tables under a new ``experience`` schema turn the *actual* data hand-offs
observed in finished runs (derived from ``control.node_attempts`` /
``topology.execution_runs``) into append-only observations, rebuildable
rollups and a state-machine of design-time-unknown relation suggestions.

Hardening (AGENTS.md / design §4, §8):
* every table is tenant-isolated (ENABLE + FORCE ROW LEVEL SECURITY);
* observation tables are append-only for the app role — SELECT/INSERT only,
  no UPDATE/DELETE (evidence is retained, never hard-deleted);
* rollup tables additionally get UPDATE/DELETE so a policy-gated ``rebuild``
  can recompute them from observations;
* suggestions only move state (SELECT/INSERT/UPDATE), they are never deleted;
* downgrade refuses to drop evidence (same stance as 0052).
"""

from alembic import op

revision = "0055_experience_layer"
down_revision = "0054_plugin_nebula_policy"
branch_labels = None
depends_on = None

# Append-only evidence: app role may only read/insert.
_APPEND_ONLY = ("edge_observations", "node_observations")
# Rebuildable rollups: app role may also update/delete (recomputable only).
_ROLLUP = ("edge_stats", "node_stats")
# State-machine working record, never deleted.
_STATEFUL = ("relation_suggestions",)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS experience")
    op.execute("GRANT USAGE ON SCHEMA experience TO audit_app")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS experience.edge_observations (
          observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          run_id uuid NOT NULL,
          trace_id text NOT NULL,
          source_plugin_id text NOT NULL,
          target_plugin_id text NOT NULL,
          contract_id text NOT NULL,
          source_attempt_id uuid NOT NULL,
          target_attempt_id uuid NOT NULL,
          source_instance text NOT NULL,
          target_instance text NOT NULL,
          source_port text,
          artifact_sha256 text,
          declared boolean NOT NULL,
          status text NOT NULL CHECK (status IN ('succeeded','failed')),
          latency_ms integer,
          observed_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, run_id, source_instance, target_instance, contract_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS experience.node_observations (
          observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          run_id uuid NOT NULL,
          trace_id text NOT NULL,
          plugin_id text NOT NULL,
          capability text NOT NULL,
          attempt_id uuid NOT NULL,
          attempt_seq integer NOT NULL DEFAULT 1,
          status text NOT NULL CHECK (status IN ('succeeded','failed')),
          error_kind text,
          latency_ms integer,
          observed_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, attempt_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS experience.edge_stats (
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          source_plugin_id text NOT NULL,
          target_plugin_id text NOT NULL,
          contract_id text NOT NULL,
          success_count integer NOT NULL DEFAULT 0,
          fail_count integer NOT NULL DEFAULT 0,
          total_latency_ms bigint NOT NULL DEFAULT 0,
          first_used_at timestamptz,
          last_used_at timestamptz,
          declared boolean NOT NULL DEFAULT false,
          confidence numeric NOT NULL DEFAULT 0,
          weight numeric NOT NULL DEFAULT 0,
          evidence_run_ids jsonb NOT NULL DEFAULT '[]',
          PRIMARY KEY (tenant_id, source_plugin_id, target_plugin_id, contract_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS experience.node_stats (
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          plugin_id text NOT NULL,
          capability text NOT NULL,
          use_count integer NOT NULL DEFAULT 0,
          success_count integer NOT NULL DEFAULT 0,
          fail_count integer NOT NULL DEFAULT 0,
          total_latency_ms bigint NOT NULL DEFAULT 0,
          error_kind_counts jsonb NOT NULL DEFAULT '{}',
          first_used_at timestamptz,
          last_used_at timestamptz,
          confidence numeric NOT NULL DEFAULT 0,
          weight numeric NOT NULL DEFAULT 0,
          PRIMARY KEY (tenant_id, plugin_id, capability)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS experience.relation_suggestions (
          suggestion_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          source_plugin_id text NOT NULL,
          target_plugin_id text NOT NULL,
          contract_id text NOT NULL,
          status text NOT NULL DEFAULT 'proposed'
            CHECK (status IN ('proposed','accepted','dismissed')),
          evidence_count integer NOT NULL DEFAULT 0,
          success_count integer NOT NULL DEFAULT 0,
          fail_count integer NOT NULL DEFAULT 0,
          confidence numeric NOT NULL DEFAULT 0,
          weight numeric NOT NULL DEFAULT 0,
          evidence_run_ids jsonb NOT NULL DEFAULT '[]',
          last_evidence_at timestamptz,
          proposed_by text NOT NULL DEFAULT 'system',
          decided_by text,
          decided_at timestamptz,
          decision_trace_id text,
          idempotency_key text NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, source_plugin_id, target_plugin_id, contract_id)
        )
        """
    )

    grants = {
        **{table: "GRANT SELECT, INSERT ON experience.{table} TO audit_app" for table in _APPEND_ONLY},
        **{table: "GRANT SELECT, INSERT, UPDATE, DELETE ON experience.{table} TO audit_app" for table in _ROLLUP},
        **{table: "GRANT SELECT, INSERT, UPDATE ON experience.{table} TO audit_app" for table in _STATEFUL},
    }
    for table, grant in grants.items():
        op.execute(f"ALTER TABLE experience.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE experience.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON experience.{table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON experience.{table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
        op.execute(grant.format(table=table))

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS edge_observations_run_idx
          ON experience.edge_observations (tenant_id, run_id);
        CREATE INDEX IF NOT EXISTS edge_observations_edge_idx
          ON experience.edge_observations
            (tenant_id, source_plugin_id, target_plugin_id, contract_id);
        CREATE INDEX IF NOT EXISTS node_observations_run_idx
          ON experience.node_observations (tenant_id, run_id);
        CREATE INDEX IF NOT EXISTS node_observations_plugin_idx
          ON experience.node_observations (tenant_id, plugin_id, capability);
        CREATE INDEX IF NOT EXISTS relation_suggestions_status_idx
          ON experience.relation_suggestions (tenant_id, status);
        """
    )


def downgrade() -> None:
    raise RuntimeError("Experience evidence is retained; archive explicitly.")
