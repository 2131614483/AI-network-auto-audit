"""Plugin Topology M8: remediation proposals, decisions and re-run lineage.

A remediation proposal is the governed disposition of an M7 ``drifted``
verification: it records the recommended action (inherited from the rollback
verdict, mutable only within the closed enum), the affected slots and the
baseline summary.  The proposal row itself is inserted exactly once (status
``pending_approval``); the *terminal* status is derived from the append-only
decision ledger (approve/reject/close) and the governed re-run lineage lives in
``remediation_run_links`` - all three surfaces are INSERT/SELECT only, RLS
FORCE protected, and never grant UPDATE/DELETE.

``topology.chain.remediate`` is seeded *inactive* so no proposal can be created
or decided unless a tenant explicitly activates it (fail-closed default).  No
remediation run may start without the M6 run gates (``topology.chain.execute``
+ ``.isolated``) - this migration adds no execution surface.
"""

from __future__ import annotations

from alembic import op

revision = "0045_remediation_proposals"
down_revision = "0044_chain_run_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- append-only remediation evidence surfaces ----------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS topology.remediation_proposals (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          verification_id uuid NOT NULL REFERENCES topology.run_verifications(id),
          run_id uuid NOT NULL REFERENCES topology.execution_runs(id),
          chain_id uuid NOT NULL REFERENCES topology.invocation_chains(id),
          chain_key text NOT NULL,
          status text NOT NULL DEFAULT 'pending_approval'
            CHECK (status IN ('pending_approval', 'approved', 'rejected', 'closed')),
          action text NOT NULL CHECK (action IN ('re-verify', 're-run-locked-release', 'escalate-human')),
          affected_slots jsonb NOT NULL,
          baseline jsonb NOT NULL,
          remediation_run_id uuid REFERENCES topology.execution_runs(id),
          reason text NOT NULL,
          idempotency_key text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, idempotency_key),
          CHECK (jsonb_array_length(affected_slots) > 0)
        );
        CREATE INDEX IF NOT EXISTS topology_remediation_proposals_chain_idx
          ON topology.remediation_proposals (tenant_id, chain_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS topology_remediation_proposals_run_idx
          ON topology.remediation_proposals (tenant_id, run_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS topology_remediation_proposals_verification_idx
          ON topology.remediation_proposals (tenant_id, verification_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS topology.remediation_decisions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          proposal_id uuid NOT NULL REFERENCES topology.remediation_proposals(id),
          decision text NOT NULL CHECK (decision IN ('approve', 'reject', 'close')),
          approver text NOT NULL,
          reason text NOT NULL DEFAULT '',
          idempotency_key text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, proposal_id, decision)
        );
        CREATE INDEX IF NOT EXISTS topology_remediation_decisions_proposal_idx
          ON topology.remediation_decisions (tenant_id, proposal_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS topology.remediation_run_links (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          proposal_id uuid NOT NULL REFERENCES topology.remediation_proposals(id),
          run_id uuid NOT NULL REFERENCES topology.execution_runs(id),
          idempotency_key text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, idempotency_key),
          UNIQUE(tenant_id, proposal_id, run_id)
        );
        CREATE INDEX IF NOT EXISTS topology_remediation_run_links_proposal_idx
          ON topology.remediation_run_links (tenant_id, proposal_id, created_at DESC);
        """
    )
    # -- RLS + grants: INSERT/SELECT only, never UPDATE/DELETE ---------------
    for table in ("remediation_proposals", "remediation_decisions", "remediation_run_links"):
        op.execute(f"ALTER TABLE topology.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE topology.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON topology.{table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON topology.{table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT ON topology.{table} TO audit_app")
    # -- remediation policy: fail-closed, seeded INACTIVE -------------------
    op.execute(
        """
        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-plugin-topology-remediate',1,'inactive',jsonb_build_array(
          jsonb_build_object('rule_id','f24c7a9b-8d0e-1f2a-3b4c-5d6e7f8a9102','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.remediate'),
              'risk_classes',jsonb_build_array('medium')))
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO NOTHING;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Topology remediation evidence is retained; archive explicitly.")