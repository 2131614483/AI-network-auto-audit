"""Plugin Topology M9: evidence chain anchors.

One explicit, user-triggered anchor appends a batch of row-hash anchors to the
tenant evidence chain (chain_key ``evidence://<tenant>/full``): each M1-M8
evidence ledger row is hashed deterministically (column-name-sorted JSON) and
linked to its predecessor via ``prev_hash``, so any tampering with an anchored
row breaks the chain at a precisely localizable link.  The anchors table is an
immutable evidence surface - RLS FORCE, INSERT/SELECT only, never UPDATE/
DELETE - and the three evidence capabilities (``topology.evidence.anchor`` /
``.verify`` / ``.export``) are seeded *inactive* so anchors, proofs and exports
require an explicit tenant grant (fail-closed default).  No child process is
ever spawned by this migration; it adds no execution surface.
"""

from __future__ import annotations

from alembic import op

revision = "0046_evidence_chain"
down_revision = "0045_remediation_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- append-only evidence chain anchors ------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS topology.evidence_chain_anchors (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          chain_key text NOT NULL,
          seq bigint NOT NULL,
          source_table text NOT NULL,
          source_pk text NOT NULL,
          row_hash text NOT NULL,
          prev_hash text NOT NULL,
          group_id uuid NOT NULL,
          anchor_scope text NOT NULL DEFAULT 'full'
            CHECK (anchor_scope IN ('full', 'topology', 'chain', 'execution', 'verification', 'remediation')),
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(chain_key, seq),
          UNIQUE(tenant_id, group_id, source_table, source_pk)
        );
        CREATE INDEX IF NOT EXISTS topology_evidence_chain_tenant_idx
          ON topology.evidence_chain_anchors (tenant_id, chain_key, seq);
        CREATE INDEX IF NOT EXISTS topology_evidence_chain_source_idx
          ON topology.evidence_chain_anchors (tenant_id, source_table, source_pk);
        CREATE INDEX IF NOT EXISTS topology_evidence_chain_group_idx
          ON topology.evidence_chain_anchors (tenant_id, group_id);
        """
    )
    # -- RLS + grants: INSERT/SELECT only, never UPDATE/DELETE -----------------
    op.execute("ALTER TABLE topology.evidence_chain_anchors ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE topology.evidence_chain_anchors FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS evidence_chain_anchors_tenant_isolation ON topology.evidence_chain_anchors")
    op.execute(
        "CREATE POLICY evidence_chain_anchors_tenant_isolation ON topology.evidence_chain_anchors "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT ON topology.evidence_chain_anchors TO audit_app")
    # -- evidence policies: fail-closed, seeded INACTIVE -----------------------
    # policy_sets is RLS + FORCE owned by audit_migrator, so the seed INSERT
    # must run with the tenant context of the target row (matches the approach
    # of the M4-M8 policy seeds under the same lock-step disclaimer).
    op.execute(
        "SELECT set_config('app.tenant_id', (SELECT id::text FROM iam.tenants WHERE slug='local-dev'), true)"
    )
    for name, rule_id, capability, risk_class, side_effect in (
        (
            "local-plugin-topology-evidence-anchor",
            "35a8c9b2-4f01-2d3e-5b4c-6a7b8c9d0e11",
            "topology.evidence.anchor",
            "medium",
            "write_data",
        ),
        (
            "local-plugin-topology-evidence-verify",
            "35a8c9b2-4f01-2d3e-5b4c-6a7b8c9d0e12",
            "topology.evidence.verify",
            "read_only",
            "read_only",
        ),
        (
            "local-plugin-topology-evidence-export",
            "35a8c9b2-4f01-2d3e-5b4c-6a7b8c9d0e13",
            "topology.evidence.export",
            "read_only",
            "read_only",
        ),
    ):
        op.execute(
            f"""
            INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
            SELECT id,'{name}',1,'inactive',jsonb_build_array(
              jsonb_build_object('rule_id','{rule_id}','effect','allow',
                'match',jsonb_build_object('capabilities',jsonb_build_array('{capability}'),
                  'risk_classes',jsonb_build_array('{risk_class}'),
                  'side_effects',jsonb_build_array('{side_effect}')))
            )
            FROM iam.tenants WHERE slug='local-dev'
            ON CONFLICT (tenant_id,name,version) DO NOTHING;
            """
        )


def downgrade() -> None:
    raise RuntimeError("Topology evidence anchor ledger is retained; archive explicitly.")