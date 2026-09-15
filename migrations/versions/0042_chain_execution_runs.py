"""Plugin Topology M6: chain-level execution runs.

Adds the orchestration control-plane for governed isolated runs.  One run
groups every per-node ledger row under a single ``run_id`` so the same chain
can be re-drilled with a fresh idempotency key (replacing the M5 single-run
``already_executed`` semantic).  ``execution_runs`` is a control surface: the
service may advance ``status`` (running -> success|failed) by CAS, but DELETE
is never granted and the evidence ledger stays INSERT/SELECT only.
"""

from __future__ import annotations

from alembic import op

revision = "0042_chain_execution_runs"
down_revision = "0041_isolated_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- control surface: one run per chain drill --------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS topology.execution_runs (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          chain_id uuid NOT NULL REFERENCES topology.invocation_chains(id),
          chain_key text NOT NULL,
          mode text NOT NULL DEFAULT 'isolated' CHECK (mode = 'isolated'),
          status text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
          reason text NOT NULL,
          idempotency_key text NOT NULL,
          trace_id text NOT NULL,
          node_total int NOT NULL DEFAULT 0,
          node_succeeded int NOT NULL DEFAULT 0,
          node_failed int NOT NULL DEFAULT 0,
          chain_checksum text NOT NULL,
          planner_version text NOT NULL,
          started_at timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS topology_execution_runs_chain_idx
          ON topology.execution_runs (tenant_id, chain_id, created_at DESC);
        """
    )
    # -- link ledger rows to their owning run ------------------------------
    op.execute(
        """
        ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS run_id uuid;
        CREATE INDEX IF NOT EXISTS topology_execution_ledger_run_idx
          ON topology.execution_ledger (tenant_id, run_id);
        -- uniqueness moves from (chain, slot, mode) to (run, slot, mode):
        -- the same chain may be drilled again under a different run, while a
        -- single run still forbids duplicate rows for the same node/slot.
        -- Postgres auto-names the M4 constraint with a 63-char truncation, so
        -- drop it by prefix instead of guessing the truncated name.
        DO $$
        DECLARE conname text;
        BEGIN
          FOR conname IN
            SELECT c.conname FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            WHERE t.relname = 'execution_ledger' AND c.contype = 'u'
              AND c.conname LIKE 'execution_ledger_tenant_id_chain_id_plan_node%'
          LOOP
            EXECUTE format('ALTER TABLE topology.execution_ledger DROP CONSTRAINT %I', conname);
          END LOOP;
        END $$;
        ALTER TABLE topology.execution_ledger
          ADD CONSTRAINT execution_ledger_run_slot_unique
          UNIQUE (tenant_id, run_id, plan_node_slot_key, ordinal, mode);
        """
    )
    # -- RLS + grants: runs allow controlled status advance, never DELETE ---
    for table, grants in (
        ("execution_runs", "SELECT, INSERT, UPDATE"),
        ("execution_ledger", "SELECT, INSERT"),
    ):
        op.execute(f"ALTER TABLE topology.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE topology.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON topology.{table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON topology.{table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
        op.execute(f"GRANT {grants} ON topology.{table} TO audit_app")


def downgrade() -> None:
    raise RuntimeError("Topology execution evidence is retained; archive explicitly.")