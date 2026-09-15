"""Plugin Topology M10: graph-driven orchestration planning.

Breaks the "intent -> plan" dead link by turning the knowledge graph into the
semantic index for orchestration planning.  ``topology.blueprint_graph_links``
finally ties graph nodes back to blueprint keys (the L2 capability / L3 domain
spaces), and ``topology.planning_intents`` records each graph-driven planning
request as an append-only evidence row (intent text, matched-node evidence,
capability requirements, plan_key, trace, idempotency key).  The graph side
gets pg_trgm GIN indexes for deterministic intent matching.

Seeds run under the ``local-dev`` tenant context (set_config first, matching
the 0033 backfill caveat) and are all ``ON CONFLICT DO NOTHING``.  The
``topology.intent.plan`` capability is seeded **inactive** (fail-closed) and
the read policy set is extended with ``topology.intent.read`` so reads never
leak without an explicit grant.  No execution surface is added here.
"""

from __future__ import annotations

from alembic import op

revision = "0048_graph_driven_planning"
down_revision = "0047_evidence_seq_explicit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # -- blueprint <-> graph binding and append-only planning intent evidence --
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS topology.blueprint_graph_links (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          blueprint_key text NOT NULL,
          graph_space_key text NOT NULL,
          node_key text NOT NULL,
          relation text NOT NULL DEFAULT 'capability'
            CHECK (relation IN ('capability', 'domain')),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, blueprint_key, node_key),
          FOREIGN KEY (tenant_id, blueprint_key)
            REFERENCES topology.plugin_blueprints(tenant_id, key)
        );
        CREATE INDEX IF NOT EXISTS topology_blueprint_graph_links_node_idx
          ON topology.blueprint_graph_links (tenant_id, graph_space_key, node_key);

        CREATE TABLE IF NOT EXISTS topology.planning_intents (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          intent_text text NOT NULL,
          matched_nodes jsonb NOT NULL DEFAULT '[]',
          capability_requirements jsonb NOT NULL DEFAULT '[]',
          plan_key text NOT NULL,
          trace_id text NOT NULL,
          idempotency_key text NOT NULL,
          reason text NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS topology_planning_intents_plan_idx
          ON topology.planning_intents (tenant_id, plan_key, created_at DESC);
        """
    )
    # -- RLS + grants ----------------------------------------------------------
    # blueprint_graph_links: SELECT/INSERT/UPDATE (admin maintains bindings).
    op.execute("ALTER TABLE topology.blueprint_graph_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE topology.blueprint_graph_links FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS blueprint_graph_links_tenant_isolation ON topology.blueprint_graph_links")
    op.execute(
        "CREATE POLICY blueprint_graph_links_tenant_isolation ON topology.blueprint_graph_links "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON topology.blueprint_graph_links TO audit_app")
    # planning_intents: append-only evidence (INSERT/SELECT only, never UPDATE/DELETE).
    op.execute("ALTER TABLE topology.planning_intents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE topology.planning_intents FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS planning_intents_tenant_isolation ON topology.planning_intents")
    op.execute(
        "CREATE POLICY planning_intents_tenant_isolation ON topology.planning_intents "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT ON topology.planning_intents TO audit_app")

    # -- deterministic intent matching indexes (pg_trgm) -----------------------
    op.execute(
        "CREATE INDEX IF NOT EXISTS graph_nodes_label_trgm_idx "
        "ON graph.nodes USING gin (label gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS graph_node_aliases_alias_trgm_idx "
        "ON graph.node_aliases USING gin (alias gin_trgm_ops)"
    )

    # -- seeds: tenant context first (0033 backfill caveat) ----------------------
    op.execute(
        """
        SELECT set_config('app.tenant_id', (SELECT id::text FROM iam.tenants WHERE slug='local-dev'), true);

        INSERT INTO graph.spaces(tenant_id,key,level,name,status)
        SELECT id,'capability-l2','L2','能力语义空间','active'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,key) DO NOTHING;

        INSERT INTO graph.space_profiles(space_id,tenant_id,graph_role,cluster_key,description)
        SELECT s.id,s.tenant_id,'capability','capability-l2',
               'M10 能力语义索引空间：组网规划的语义索引'
        FROM graph.spaces s WHERE s.key='capability-l2'
        ON CONFLICT (space_id) DO NOTHING;

        INSERT INTO graph.spaces(tenant_id,key,level,name,status)
        SELECT id,'audit-l3','L3','审计业务域空间','active'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,key) DO NOTHING;

        INSERT INTO graph.space_profiles(space_id,tenant_id,graph_role,cluster_key,description)
        SELECT s.id,s.tenant_id,'domain','audit-l3',
               'M10 审计业务域空间：经 capability_contract 桥接扩展能力'
        FROM graph.spaces s WHERE s.key='audit-l3'
        ON CONFLICT (space_id) DO NOTHING;

        INSERT INTO graph.nodes(tenant_id,space_id,canonical_key,node_type,label,properties)
        SELECT t.id,s.id,'capability:audit.ledger.validate','capability','总账质量校验',
               jsonb_build_object('capability','audit.ledger.validate','family','audit')
        FROM iam.tenants t JOIN graph.spaces s ON s.tenant_id=t.id AND s.key='capability-l2'
        WHERE t.slug='local-dev'
        ON CONFLICT (space_id,canonical_key) DO NOTHING;

        INSERT INTO graph.nodes(tenant_id,space_id,canonical_key,node_type,label,properties)
        SELECT t.id,s.id,'capability:audit.finding.draft','capability','审计发现草稿',
               jsonb_build_object('capability','audit.finding.draft','family','audit')
        FROM iam.tenants t JOIN graph.spaces s ON s.tenant_id=t.id AND s.key='capability-l2'
        WHERE t.slug='local-dev'
        ON CONFLICT (space_id,canonical_key) DO NOTHING;

        INSERT INTO graph.nodes(tenant_id,space_id,canonical_key,node_type,label,properties)
        SELECT t.id,s.id,'capability:quant.research-note.draft','capability','量化研究结论',
               jsonb_build_object('capability','quant.research-note.draft','family','quant')
        FROM iam.tenants t JOIN graph.spaces s ON s.tenant_id=t.id AND s.key='capability-l2'
        WHERE t.slug='local-dev'
        ON CONFLICT (space_id,canonical_key) DO NOTHING;

        INSERT INTO graph.nodes(tenant_id,space_id,canonical_key,node_type,label,properties)
        SELECT t.id,s.id,'domain:financial-audit','domain','财务审计',
               jsonb_build_object('domain','financial-audit')
        FROM iam.tenants t JOIN graph.spaces s ON s.tenant_id=t.id AND s.key='audit-l3'
        WHERE t.slug='local-dev'
        ON CONFLICT (space_id,canonical_key) DO NOTHING;

        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'总账校验','name',1
        FROM graph.nodes n WHERE n.canonical_key='capability:audit.ledger.validate'
        ON CONFLICT (node_id,alias) DO NOTHING;
        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'总账质量','name',1
        FROM graph.nodes n WHERE n.canonical_key='capability:audit.ledger.validate'
        ON CONFLICT (node_id,alias) DO NOTHING;
        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'Ledger Quality Validation','name',0.9
        FROM graph.nodes n WHERE n.canonical_key='capability:audit.ledger.validate'
        ON CONFLICT (node_id,alias) DO NOTHING;

        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'审计发现','name',1
        FROM graph.nodes n WHERE n.canonical_key='capability:audit.finding.draft'
        ON CONFLICT (node_id,alias) DO NOTHING;
        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'发现草稿','name',1
        FROM graph.nodes n WHERE n.canonical_key='capability:audit.finding.draft'
        ON CONFLICT (node_id,alias) DO NOTHING;
        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'Audit Finding Draft','name',0.9
        FROM graph.nodes n WHERE n.canonical_key='capability:audit.finding.draft'
        ON CONFLICT (node_id,alias) DO NOTHING;

        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'量化研究','name',1
        FROM graph.nodes n WHERE n.canonical_key='capability:quant.research-note.draft'
        ON CONFLICT (node_id,alias) DO NOTHING;
        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'研究结论','name',1
        FROM graph.nodes n WHERE n.canonical_key='capability:quant.research-note.draft'
        ON CONFLICT (node_id,alias) DO NOTHING;
        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'Quant Research Note','name',0.9
        FROM graph.nodes n WHERE n.canonical_key='capability:quant.research-note.draft'
        ON CONFLICT (node_id,alias) DO NOTHING;

        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'审计域','name',1
        FROM graph.nodes n WHERE n.canonical_key='domain:financial-audit'
        ON CONFLICT (node_id,alias) DO NOTHING;
        INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
        SELECT n.tenant_id,n.id,'Financial Audit','name',0.9
        FROM graph.nodes n WHERE n.canonical_key='domain:financial-audit'
        ON CONFLICT (node_id,alias) DO NOTHING;

        INSERT INTO graph.edges(tenant_id,space_id,source_node_id,target_node_id,relation_type,weight)
        SELECT n1.tenant_id,n1.space_id,n1.id,n2.id,'depends_on',1
        FROM graph.nodes n1 JOIN graph.nodes n2 ON n2.tenant_id=n1.tenant_id
             AND n2.canonical_key='capability:audit.finding.draft'
        WHERE n1.canonical_key='capability:audit.ledger.validate'
        ON CONFLICT (space_id,source_node_id,target_node_id,relation_type) DO NOTHING;

        INSERT INTO graph.bridge_rules(tenant_id,source_space_id,target_space_id,relation_type,status,max_weight)
        SELECT t.id,s1.id,s2.id,'capability_contract','active',1
        FROM iam.tenants t
          JOIN graph.spaces s1 ON s1.tenant_id=t.id AND s1.key='audit-l3'
          JOIN graph.spaces s2 ON s2.tenant_id=t.id AND s2.key='capability-l2'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,source_space_id,target_space_id,relation_type) DO NOTHING;

        INSERT INTO graph.bridge_edges(tenant_id,source_space_id,target_space_id,source_node_id,target_node_id,relation_type,weight,status)
        SELECT n1.tenant_id,s1.id,s2.id,n1.id,n2.id,'capability_contract',1,'active'
        FROM graph.nodes n1
          JOIN graph.spaces s1 ON s1.id=n1.space_id AND s1.key='audit-l3'
          JOIN graph.nodes n2 ON n2.tenant_id=n1.tenant_id
               AND n2.canonical_key='capability:audit.ledger.validate'
          JOIN graph.spaces s2 ON s2.id=n2.space_id AND s2.key='capability-l2'
        WHERE n1.canonical_key='domain:financial-audit'
        ON CONFLICT (source_node_id,target_node_id,relation_type) DO NOTHING;

        INSERT INTO topology.blueprint_graph_links(tenant_id,blueprint_key,graph_space_key,node_key,relation)
        SELECT id,'ledger-quality-slot','capability-l2','capability:audit.ledger.validate','capability'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,blueprint_key,node_key) DO NOTHING;

        INSERT INTO topology.blueprint_graph_links(tenant_id,blueprint_key,graph_space_key,node_key,relation)
        SELECT id,'finding-draft-slot','capability-l2','capability:audit.finding.draft','capability'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,blueprint_key,node_key) DO NOTHING;

        INSERT INTO topology.blueprint_graph_links(tenant_id,blueprint_key,graph_space_key,node_key,relation)
        SELECT id,'research-note-slot','capability-l2','capability:quant.research-note.draft','capability'
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,blueprint_key,node_key) DO NOTHING;

        -- finding-draft-slot needs a cluster membership so every linked
        -- blueprint is reachable by the planner (multi-axis convergence).
        INSERT INTO topology.cluster_memberships(tenant_id,blueprint_id,cluster_id,axis)
        SELECT t.id,b.id,c.id,'capability'
        FROM iam.tenants t
          JOIN topology.plugin_blueprints b ON b.tenant_id=t.id AND b.key='finding-draft-slot'
          JOIN topology.plugin_clusters c ON c.tenant_id=t.id AND c.key='capability-audit-quality'
        WHERE t.slug='local-dev'
        ON CONFLICT (tenant_id,blueprint_id,cluster_id) DO NOTHING;
        """
    )

    # -- policies ----------------------------------------------------------------
    # Re-seed the read policy set so topology.intent.read is explicitly active
    # (rule_id 8fd9e0f1... has been part of local-plugin-topology-read since M4).
    op.execute(
        """
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
              'risk_classes',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','7ec8d9e0-1f2a-3b4c-5d6e-7f8a9b0c1d2e','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.read'),
              'risk_classes',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','8fd9e0f1-2a3b-4c5d-6e7f-8a9b0c1d2e3f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.intent.read'),
              'risk_classes',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','9eaf0b1c-3a4b-5c6d-7e8f-9a0b1c2d3e4f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.chain.write'),
              'risk_classes',jsonb_build_array('low')))
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO UPDATE
          SET status=EXCLUDED.status,rules=EXCLUDED.rules;
        """
    )
    # New graph-planning policy set: topology.intent.plan is fail-closed
    # (status=inactive until the acceptance script explicitly enables it).
    op.execute(
        """
        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-plugin-topology-graph-plan',1,'inactive',jsonb_build_array(
          jsonb_build_object('rule_id','b1c2d3e4-5a6b-7c8d-9e0f-1a2b3c4d5e6f','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.intent.plan'),
              'risk_classes',jsonb_build_array('low'),
              'side_effects',jsonb_build_array('write_data')))
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO NOTHING;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Graph planning bindings and intent evidence are retained; archive explicitly.")
