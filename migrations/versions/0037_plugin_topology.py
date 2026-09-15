"""Create the isolated Plugin Topology module (M1).

Topology keeps planning separate from execution: clusters/blueprints are
L0/L1/L2 slots only, routing plans are forever plan_only, releases and the
recycle bin are versioned and never hard-deleted.  No plugin code, command,
secret, domain payload or execution queue is created here.
"""

import hashlib

from alembic import op

revision = "0037_plugin_topology"
down_revision = "0036_aiops_exec_verification"
branch_labels = None
depends_on = None

_TOPOLOGY_TABLES = (
    "plugin_clusters",
    "cluster_versions",
    "plugin_blueprints",
    "blueprint_versions",
    "cluster_memberships",
    "interface_contracts",
    "compatibility_results",
    "topology_edges",
    "domain_bridges",
    "topology_releases",
    "routing_plans",
    "routing_plan_nodes",
    "routing_plan_edges",
    "manifest_bindings",
    "configuration_versions",
    "runtime_overrides",
    "health_snapshot_refs",
    "topology_recycle_bin",
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS topology")
    op.execute("GRANT USAGE ON SCHEMA topology TO audit_app")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS topology.plugin_clusters (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          key text NOT NULL,
          name text NOT NULL,
          parent_cluster_id uuid REFERENCES topology.plugin_clusters(id),
          cluster_kind text NOT NULL CHECK (cluster_kind IN
            ('business_domain','governance_zone','capability_family','runtime_pool')),
          description text NOT NULL DEFAULT '',
          routing_budget jsonb NOT NULL DEFAULT '{}',
          allowed_bridge_kinds jsonb NOT NULL DEFAULT '[]',
          status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','retired')),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, key),
          CONSTRAINT cluster_parent_not_self CHECK (parent_cluster_id IS NULL OR parent_cluster_id <> id)
        );
        CREATE INDEX IF NOT EXISTS topology_plugin_clusters_kind_idx
          ON topology.plugin_clusters (tenant_id, cluster_kind, status);

        CREATE TABLE IF NOT EXISTS topology.cluster_versions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          cluster_id uuid NOT NULL REFERENCES topology.plugin_clusters(id),
          version text NOT NULL,
          snapshot jsonb NOT NULL,
          checksum text NOT NULL,
          status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','retired')),
          created_by uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, cluster_id, version)
        );

        CREATE TABLE IF NOT EXISTS topology.plugin_blueprints (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          primary_cluster_id uuid NOT NULL REFERENCES topology.plugin_clusters(id),
          key text NOT NULL,
          name text NOT NULL,
          blueprint_type text NOT NULL CHECK (blueprint_type IN ('capability','producer','consumer')),
          capability_contract jsonb NOT NULL,
          source_refs jsonb NOT NULL DEFAULT '[]',
          status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','released','archived')),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, key)
        );
        CREATE INDEX IF NOT EXISTS topology_plugin_blueprints_cluster_idx
          ON topology.plugin_blueprints (tenant_id, primary_cluster_id, status);

        CREATE TABLE IF NOT EXISTS topology.blueprint_versions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          version text NOT NULL,
          contract_snapshot jsonb NOT NULL,
          checksum text NOT NULL,
          status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','retired')),
          created_by uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, blueprint_id, version)
        );

        CREATE TABLE IF NOT EXISTS topology.cluster_memberships (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          cluster_id uuid NOT NULL REFERENCES topology.plugin_clusters(id),
          axis text NOT NULL CHECK (axis IN ('business','capability','resource','governance')),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, blueprint_id, cluster_id)
        );
        CREATE INDEX IF NOT EXISTS topology_cluster_memberships_axis_idx
          ON topology.cluster_memberships (tenant_id, cluster_id, axis);

        CREATE TABLE IF NOT EXISTS topology.interface_contracts (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          contract_id text NOT NULL,
          contract_version text NOT NULL,
          kind text NOT NULL CHECK (kind IN ('input','output','event')),
          format text NOT NULL DEFAULT 'json',
          classification text NOT NULL DEFAULT 'internal',
          schema_ref text NOT NULL,
          fields jsonb NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, contract_id, contract_version, kind)
        );

        CREATE TABLE IF NOT EXISTS topology.compatibility_results (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          producer_contract_id uuid NOT NULL REFERENCES topology.interface_contracts(id),
          consumer_contract_id uuid NOT NULL REFERENCES topology.interface_contracts(id),
          compatible boolean NOT NULL,
          detail jsonb NOT NULL DEFAULT '{}',
          computed_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, producer_contract_id, consumer_contract_id)
        );

        CREATE TABLE IF NOT EXISTS topology.topology_edges (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          source_blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          target_blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          relation_type text NOT NULL CHECK (relation_type IN ('depends_on','data_flow','fallback','bridge')),
          condition_ref text,
          weight numeric NOT NULL DEFAULT 1 CHECK (weight > 0 AND weight <= 1),
          status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, source_blueprint_id, target_blueprint_id, relation_type),
          CONSTRAINT edge_not_self CHECK (source_blueprint_id <> target_blueprint_id)
        );
        CREATE INDEX IF NOT EXISTS topology_edges_source_idx
          ON topology.topology_edges (tenant_id, source_blueprint_id, relation_type, status);
        CREATE INDEX IF NOT EXISTS topology_edges_target_idx
          ON topology.topology_edges (tenant_id, target_blueprint_id, relation_type, status);

        CREATE TABLE IF NOT EXISTS topology.domain_bridges (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          ref_kind text NOT NULL CHECK (ref_kind IN
            ('artifact_ref','capability_contract','released_graph_ref','health_signal')),
          bridge_ref text NOT NULL,
          status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, blueprint_id, ref_kind)
        );

        CREATE TABLE IF NOT EXISTS topology.topology_releases (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          release_key text NOT NULL,
          version text NOT NULL,
          catalog_checksum text NOT NULL,
          blueprint_snapshots jsonb NOT NULL,
          edge_snapshots jsonb NOT NULL,
          status text NOT NULL DEFAULT 'draft' CHECK (status IN
            ('draft','published','superseded','rolled_back')),
          created_by uuid,
          trace_id text,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, release_key, version)
        );
        CREATE INDEX IF NOT EXISTS topology_topology_releases_status_idx
          ON topology.topology_releases (tenant_id, status, created_at DESC);

        CREATE TABLE IF NOT EXISTS topology.routing_plans (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          plan_key text NOT NULL,
          mission_key text NOT NULL,
          mode text NOT NULL DEFAULT 'plan_only' CHECK (mode = 'plan_only'),
          plan_json jsonb NOT NULL,
          topology_release_id uuid NOT NULL REFERENCES topology.topology_releases(id),
          planner_version text NOT NULL,
          health_snapshot_ref text,
          budget jsonb NOT NULL DEFAULT '{}',
          checksum text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, plan_key)
        );
        CREATE INDEX IF NOT EXISTS topology_routing_plans_mission_idx
          ON topology.routing_plans (tenant_id, mission_key, created_at DESC);

        CREATE TABLE IF NOT EXISTS topology.routing_plan_nodes (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          plan_id uuid NOT NULL REFERENCES topology.routing_plans(id),
          blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          slot_key text NOT NULL,
          alternatives jsonb NOT NULL DEFAULT '[]',
          UNIQUE(tenant_id, plan_id, slot_key)
        );

        CREATE TABLE IF NOT EXISTS topology.routing_plan_edges (
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          plan_id uuid NOT NULL REFERENCES topology.routing_plans(id),
          source_node text NOT NULL,
          target_node text NOT NULL,
          relation_type text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY(plan_id, source_node, target_node),
          CONSTRAINT plan_edge_not_self CHECK (source_node <> target_node)
        );

        CREATE TABLE IF NOT EXISTS topology.manifest_bindings (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          plugin_version_id uuid NOT NULL REFERENCES catalog.plugin_versions(id),
          manifest_sha256 text NOT NULL,
          status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','revoked')),
          approved_by uuid,
          approved_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, blueprint_id, plugin_version_id)
        );
        CREATE INDEX IF NOT EXISTS topology_manifest_bindings_bp_idx
          ON topology.manifest_bindings (tenant_id, blueprint_id, status);

        CREATE TABLE IF NOT EXISTS topology.configuration_versions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          version text NOT NULL,
          values jsonb NOT NULL,
          origin text NOT NULL CHECK (origin IN ('blueprint_default','custom')),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, blueprint_id, version)
        );

        CREATE TABLE IF NOT EXISTS topology.runtime_overrides (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          field text NOT NULL,
          value jsonb NOT NULL,
          ttl_expires_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, blueprint_id, field)
        );

        CREATE TABLE IF NOT EXISTS topology.health_snapshot_refs (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          blueprint_id uuid NOT NULL REFERENCES topology.plugin_blueprints(id),
          snapshot_ref text NOT NULL,
          source text NOT NULL CHECK (source IN ('registry','aiops')),
          observed_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, blueprint_id)
        );

        CREATE TABLE IF NOT EXISTS topology.topology_recycle_bin (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          entity_kind text NOT NULL CHECK (entity_kind IN
            ('cluster','blueprint','edge','membership','binding')),
          entity_id uuid NOT NULL,
          snapshot jsonb NOT NULL,
          reason text NOT NULL DEFAULT '',
          recycle_type text NOT NULL CHECK (recycle_type IN ('recycled','restored')),
          restored_from uuid,
          created_by uuid,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS topology_recycle_bin_kind_idx
          ON topology.topology_recycle_bin (tenant_id, entity_kind, created_at DESC);
        """
    )

    for table in _TOPOLOGY_TABLES:
        op.execute(f"ALTER TABLE topology.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE topology.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON topology.{table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON topology.{table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
        grant = "GRANT SELECT, INSERT, UPDATE ON topology.{table} TO audit_app"
        if table == "topology_recycle_bin":
            grant = "GRANT SELECT, INSERT, UPDATE, DELETE ON topology.{table} TO audit_app"
        op.execute(grant.format(table=table))

    seed_checksum = hashlib.sha256(b"m1-seed-catalog:v1").hexdigest()
    op.execute(
        f"""
        SELECT set_config('app.tenant_id', id::text, true) FROM iam.tenants WHERE slug='local-dev';

        INSERT INTO topology.plugin_clusters(tenant_id,key,name,cluster_kind,routing_budget,allowed_bridge_kinds,status)
        SELECT id,'business-financial-audit','财务审计业务域','business_domain',
               jsonb_build_object('max_candidates',8,'max_chain_length',4,'max_latency_ms',5000),
               jsonb_build_array('artifact_ref','capability_contract'),'active'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,key) DO NOTHING;

        INSERT INTO topology.plugin_clusters(tenant_id,key,name,cluster_kind,routing_budget,allowed_bridge_kinds,status)
        SELECT id,'capability-audit-quality','审计质量能力族','capability_family',
               jsonb_build_object('max_candidates',8,'max_chain_length',4,'max_latency_ms',5000),
               jsonb_build_array(),'active'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,key) DO NOTHING;

        INSERT INTO topology.plugin_clusters(tenant_id,key,name,cluster_kind,routing_budget,allowed_bridge_kinds,status)
        SELECT id,'pool-local-cpu','本地 CPU 运行池','runtime_pool',
               jsonb_build_object('max_candidates',16,'max_chain_length',8,'max_latency_ms',10000),
               jsonb_build_array(),'active'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,key) DO NOTHING;

        INSERT INTO topology.plugin_clusters(tenant_id,key,name,cluster_kind,routing_budget,allowed_bridge_kinds,status)
        SELECT id,'governance-local-dev-approved','本地开发已批准治理区','governance_zone',
               jsonb_build_object('max_candidates',16,'max_chain_length',8,'max_latency_ms',10000),
               jsonb_build_array('artifact_ref','capability_contract','released_graph_ref','health_signal'),'active'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,key) DO NOTHING;

        INSERT INTO topology.plugin_blueprints(tenant_id,primary_cluster_id,key,name,blueprint_type,capability_contract,source_refs)
        SELECT t.id,c.id,'ledger-quality-slot','总账质量校验槽位','capability',
               jsonb_build_object('capability','audit.ledger.validate','version','1.0.0',
                 'inputs',jsonb_build_array('ledger'),
                 'outputs',jsonb_build_array('audit-quality-candidates')),
               jsonb_build_array(jsonb_build_object('kind','design_document','uri','docs/审计组网插件规划-v1.md#r1'))
        FROM iam.tenants t JOIN topology.plugin_clusters c ON c.tenant_id=t.id AND c.key='business-financial-audit'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,key) DO NOTHING;

        INSERT INTO topology.plugin_blueprints(tenant_id,primary_cluster_id,key,name,blueprint_type,capability_contract,source_refs)
        SELECT t.id,c.id,'finding-draft-slot','审计发现草稿槽位','capability',
               jsonb_build_object('capability','audit.finding.draft','version','1.0.0',
                 'inputs',jsonb_build_array('candidates'),
                 'outputs',jsonb_build_array('finding-draft')),
               jsonb_build_array(jsonb_build_object('kind','design_document','uri','docs/审计组网插件规划-v1.md#r2'))
        FROM iam.tenants t JOIN topology.plugin_clusters c ON c.tenant_id=t.id AND c.key='capability-audit-quality'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,key) DO NOTHING;

        INSERT INTO topology.plugin_blueprints(tenant_id,primary_cluster_id,key,name,blueprint_type,capability_contract,source_refs)
        SELECT t.id,c.id,'research-note-slot','量化研究结论草稿槽位','capability',
               jsonb_build_object('capability','quant.research-note.draft','version','1.0.0',
                 'outputs',jsonb_build_array('research-note-draft')),
               jsonb_build_array(jsonb_build_object('kind','design_document','uri','docs/审计组网插件规划-v1.md#r3'))
        FROM iam.tenants t JOIN topology.plugin_clusters c ON c.tenant_id=t.id AND c.key='governance-local-dev-approved'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,key) DO NOTHING;

        INSERT INTO topology.cluster_memberships(tenant_id,blueprint_id,cluster_id,axis)
        SELECT t.id,b.id,c.id,'capability'
        FROM iam.tenants t
          JOIN topology.plugin_blueprints b ON b.tenant_id=t.id AND b.key='ledger-quality-slot'
          JOIN topology.plugin_clusters c ON c.tenant_id=t.id AND c.key='capability-audit-quality'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,blueprint_id,cluster_id) DO NOTHING;

        INSERT INTO topology.cluster_memberships(tenant_id,blueprint_id,cluster_id,axis)
        SELECT t.id,b.id,c.id,'resource'
        FROM iam.tenants t
          JOIN topology.plugin_blueprints b ON b.tenant_id=t.id AND b.key='ledger-quality-slot'
          JOIN topology.plugin_clusters c ON c.tenant_id=t.id AND c.key='pool-local-cpu'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,blueprint_id,cluster_id) DO NOTHING;

        INSERT INTO topology.topology_edges(tenant_id,source_blueprint_id,target_blueprint_id,relation_type)
        SELECT t.id,s.id,tt.id,'depends_on'
        FROM iam.tenants t
          JOIN topology.plugin_blueprints s ON s.tenant_id=t.id AND s.key='ledger-quality-slot'
          JOIN topology.plugin_blueprints tt ON tt.tenant_id=t.id AND tt.key='finding-draft-slot'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,source_blueprint_id,target_blueprint_id,relation_type) DO NOTHING;

        INSERT INTO topology.interface_contracts(tenant_id,contract_id,contract_version,kind,format,classification,schema_ref)
        SELECT id,'incident-proposal','1.0.0','input','json','internal',
               'contracts/jsonschema/incident-proposal.schema.json'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,contract_id,contract_version,kind) DO NOTHING;

        INSERT INTO topology.interface_contracts(tenant_id,contract_id,contract_version,kind,format,classification,schema_ref)
        SELECT id,'experiment-evaluation','1.0.0','input','json','restricted',
               'contracts/jsonschema/experiment-evaluation.schema.json'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,contract_id,contract_version,kind) DO NOTHING;

        INSERT INTO topology.topology_releases(tenant_id,release_key,version,catalog_checksum,blueprint_snapshots,edge_snapshots,status)
        SELECT id,'m1-seed-release','1.0.0','{seed_checksum}',
               jsonb_build_object(
                 'ledger-quality-slot', jsonb_build_object('capability','audit.ledger.validate','version','1.0.0'),
                 'finding-draft-slot', jsonb_build_object('capability','audit.finding.draft','version','1.0.0'),
                 'research-note-slot', jsonb_build_object('capability','quant.research-note.draft','version','1.0.0')
               ),
               jsonb_build_object('ledger-quality-slot_to_finding-draft-slot', 'depends_on'),
               'published'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,release_key,version) DO NOTHING;

        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-plugin-topology-read',1,'active',jsonb_build_array(
          jsonb_build_object('rule_id','3c8d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.cluster.read'))),
          jsonb_build_object('rule_id','4d9e5f6a-7b8c-9d0e-1f2a-3b4c5d6e7f80','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.blueprint.read'))),
          jsonb_build_object('rule_id','5eaf6b7c-8d9e-0f1a-2b3c-4d5e6f7a8b9c','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.plan.read')))
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO UPDATE
          SET status=EXCLUDED.status,rules=EXCLUDED.rules;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Topology releases and recycle history are retained; archive explicitly.")
