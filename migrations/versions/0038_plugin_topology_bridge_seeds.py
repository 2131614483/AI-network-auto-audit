"""M2 Plugin Topology bridge seeds.

Adds the public cross-domain bridge registration for ledger-quality-slot and a
bridge-typed edge to the research-note slot, plus the read-only
``topology.bridge.read`` policy rule.  All inserts are idempotent so rerunning
the migration never duplicates rows or touches evidence.
"""

from alembic import op

revision = "0038_topology_bridge_seeds"
down_revision = "0037_plugin_topology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        SELECT set_config('app.tenant_id', id::text, true) FROM iam.tenants WHERE slug='local-dev';

        INSERT INTO topology.domain_bridges(tenant_id,blueprint_id,ref_kind,bridge_ref,status)
        SELECT t.id,b.id,'capability_contract','contracts/jsonschema/audit-quality-candidates@1','active'
        FROM iam.tenants t
          JOIN topology.plugin_blueprints b ON b.tenant_id=t.id AND b.key='ledger-quality-slot'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,blueprint_id,ref_kind) DO NOTHING;

        INSERT INTO topology.cluster_memberships(tenant_id,blueprint_id,cluster_id,axis)
        SELECT t.id,b.id,c.id,'governance'
        FROM iam.tenants t
          JOIN topology.plugin_blueprints b ON b.tenant_id=t.id AND b.key='research-note-slot'
          JOIN topology.plugin_clusters c ON c.tenant_id=t.id AND c.key='governance-local-dev-approved'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,blueprint_id,cluster_id) DO NOTHING;

        INSERT INTO topology.topology_edges(tenant_id,source_blueprint_id,target_blueprint_id,relation_type)
        SELECT t.id,s.id,tt.id,'bridge'
        FROM iam.tenants t
          JOIN topology.plugin_blueprints s ON s.tenant_id=t.id AND s.key='ledger-quality-slot'
          JOIN topology.plugin_blueprints tt ON tt.tenant_id=t.id AND tt.key='research-note-slot'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,source_blueprint_id,target_blueprint_id,relation_type) DO NOTHING;

        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-plugin-topology-read',1,'active',jsonb_build_array(
          jsonb_build_object('rule_id','3c8d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.cluster.read'))),
          jsonb_build_object('rule_id','4d9e5f6a-7b8c-9d0e-1f2a-3b4c5d6e7f80','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.blueprint.read'))),
          jsonb_build_object('rule_id','5eaf6b7c-8d9e-0f1a-2b3c-4d5e6f7a8b9c','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.plan.read'))),
          jsonb_build_object('rule_id','6fb7c8d9-0e1f-2a3b-4c5d-6e7f8a9b0c1d','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.bridge.read'),
              'risk_classes',jsonb_build_array('read_only')))
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO UPDATE
          SET status=EXCLUDED.status,rules=EXCLUDED.rules;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Bridge registrations and policies are evidence; archive explicitly.")