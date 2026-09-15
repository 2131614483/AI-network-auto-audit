"""Plugin Topology M7: run verifications (reproducibility check + rollback verdict).

A verification is an append-only read-only evidence row: it compares the
per-node ``output_checksum`` ledger rows of one ``success`` isolated run
against a reference run (explicit id or the chain's latest success run) and
records ``verified`` or ``drifted``.  When drifted, a *read-only* rollback
verdict (action/affected_slots/baseline) is stored - advisory only, the table
never grants UPDATE/DELETE and no child process is ever spawned by the
comparator (pure DB reads).

``topology.chain.verify`` is seeded *inactive* so nothing can create a
verification unless a tenant explicitly activates it (fail-closed default).
"""

from __future__ import annotations

from alembic import op

revision = "0044_chain_run_verification"
down_revision = "0043_chain_run_exclusive_running"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- append-only verification evidence surface --------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS topology.run_verifications (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          run_id uuid NOT NULL REFERENCES topology.execution_runs(id),
          chain_id uuid NOT NULL REFERENCES topology.invocation_chains(id),
          chain_key text NOT NULL,
          reference_run_id uuid REFERENCES topology.execution_runs(id),
          status text NOT NULL CHECK (status IN ('verified', 'drifted')),
          node_total int NOT NULL DEFAULT 0,
          node_matched int NOT NULL DEFAULT 0,
          node_mismatched int NOT NULL DEFAULT 0,
          node_ref_missing int NOT NULL DEFAULT 0,
          reason text NOT NULL,
          rollback_verdict jsonb,
          idempotency_key text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, idempotency_key),
          CHECK (node_matched + node_mismatched + node_ref_missing = node_total)
        );
        CREATE INDEX IF NOT EXISTS topology_run_verifications_run_idx
          ON topology.run_verifications (tenant_id, run_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS topology_run_verifications_chain_idx
          ON topology.run_verifications (tenant_id, chain_id, created_at DESC);
        """
    )
    # -- RLS + grants: INSERT/SELECT only, never UPDATE/DELETE ---------------
    op.execute("ALTER TABLE topology.run_verifications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE topology.run_verifications FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS run_verifications_tenant_isolation ON topology.run_verifications")
    op.execute(
        "CREATE POLICY run_verifications_tenant_isolation ON topology.run_verifications "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT ON topology.run_verifications TO audit_app")
    # -- verification policy: fail-closed, seeded INACTIVE ------------------
    op.execute(
        """
        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-plugin-topology-verify',1,'inactive',jsonb_build_array(
          jsonb_build_object('rule_id','e13b6f8a-7c9d-0f1e-2a3b-4c5d6e7f8190','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.verify'),
              'risk_classes',jsonb_build_array('medium')))
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO NOTHING;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Topology verification evidence is retained; archive explicitly.")