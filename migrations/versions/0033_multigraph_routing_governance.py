"""Enforce registered cross-graph bridges and preserve node versions.

Phase 4 deliberately keeps graph spaces separated.  A bridge is permitted
only when a tenant explicitly registers one of the narrow control-plane
relations; it is never a substitute for an unrestricted cross-domain edge.
"""

from alembic import op

revision = "0033_multigraph_governance"
down_revision = "0032_rich_media_job_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS graph.space_profiles (
          space_id uuid PRIMARY KEY REFERENCES graph.spaces(id),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          graph_role text NOT NULL CHECK (graph_role IN ('cluster','blueprint','capability','domain','runtime_evidence')),
          cluster_key text NOT NULL,
          description text NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS graph_space_profiles_tenant_cluster_idx
          ON graph.space_profiles (tenant_id, cluster_key, graph_role);

        CREATE TABLE IF NOT EXISTS graph.bridge_rules (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          source_space_id uuid NOT NULL REFERENCES graph.spaces(id),
          target_space_id uuid NOT NULL REFERENCES graph.spaces(id),
          relation_type text NOT NULL CHECK (relation_type IN ('artifact_ref','capability_contract','released_graph_ref','health_signal')),
          status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
          max_weight numeric NOT NULL DEFAULT 1 CHECK (max_weight >= 0 AND max_weight <= 1),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id, source_space_id, target_space_id, relation_type)
        );
        CREATE INDEX IF NOT EXISTS graph_bridge_rules_route_idx
          ON graph.bridge_rules (tenant_id, source_space_id, status, relation_type);

        CREATE TABLE IF NOT EXISTS graph.node_revisions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          node_id uuid NOT NULL REFERENCES graph.nodes(id),
          row_version integer NOT NULL,
          event_type text NOT NULL CHECK (event_type IN ('update','recycled','restored','rollback')),
          snapshot jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(node_id, row_version)
        );
        CREATE INDEX IF NOT EXISTS graph_node_revisions_lookup_idx
          ON graph.node_revisions (tenant_id, node_id, row_version DESC);

        ALTER TABLE graph.space_profiles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE graph.space_profiles FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS space_profiles_tenant_isolation ON graph.space_profiles;
        CREATE POLICY space_profiles_tenant_isolation ON graph.space_profiles
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE graph.bridge_rules ENABLE ROW LEVEL SECURITY;
        ALTER TABLE graph.bridge_rules FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS bridge_rules_tenant_isolation ON graph.bridge_rules;
        CREATE POLICY bridge_rules_tenant_isolation ON graph.bridge_rules
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE graph.node_revisions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE graph.node_revisions FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS node_revisions_tenant_isolation ON graph.node_revisions;
        CREATE POLICY node_revisions_tenant_isolation ON graph.node_revisions
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        GRANT SELECT, INSERT, UPDATE ON graph.space_profiles, graph.bridge_rules, graph.node_revisions TO audit_app;

        CREATE OR REPLACE FUNCTION graph.capture_node_revision()
        RETURNS trigger LANGUAGE plpgsql AS $fn$
        DECLARE revision_event text;
        BEGIN
          IF ROW(OLD.canonical_key, OLD.node_type, OLD.label, OLD.properties, OLD.valid_from, OLD.valid_to, OLD.deleted_at)
             IS NOT DISTINCT FROM
             ROW(NEW.canonical_key, NEW.node_type, NEW.label, NEW.properties, NEW.valid_from, NEW.valid_to, NEW.deleted_at) THEN
            RETURN NEW;
          END IF;
          revision_event := COALESCE(NULLIF(current_setting('app.graph_revision_event', true), ''), 'update');
          INSERT INTO graph.node_revisions(tenant_id,node_id,row_version,event_type,snapshot)
          VALUES(
            OLD.tenant_id, OLD.id, OLD.row_version, revision_event,
            jsonb_build_object(
              'canonical_key', OLD.canonical_key, 'node_type', OLD.node_type, 'label', OLD.label,
              'properties', OLD.properties, 'valid_from', OLD.valid_from, 'valid_to', OLD.valid_to,
              'deleted_at', OLD.deleted_at, 'space_id', OLD.space_id, 'row_version', OLD.row_version
            )
          ) ON CONFLICT (node_id,row_version) DO NOTHING;
          RETURN NEW;
        END;
        $fn$;
        DROP TRIGGER IF EXISTS graph_nodes_capture_revision ON graph.nodes;
        CREATE TRIGGER graph_nodes_capture_revision
          BEFORE UPDATE ON graph.nodes
          FOR EACH ROW EXECUTE FUNCTION graph.capture_node_revision();

        CREATE OR REPLACE FUNCTION graph.validate_registered_bridge()
        RETURNS trigger LANGUAGE plpgsql AS $fn$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM graph.bridge_rules rule
            WHERE rule.tenant_id=NEW.tenant_id
              AND rule.source_space_id=NEW.source_space_id
              AND rule.target_space_id=NEW.target_space_id
              AND rule.relation_type=NEW.relation_type
              AND rule.status='active'
              AND NEW.weight <= rule.max_weight
          ) THEN
            RAISE EXCEPTION 'bridge edge requires an active registered bridge rule';
          END IF;
          RETURN NEW;
        END;
        $fn$;
        DROP TRIGGER IF EXISTS bridge_edges_registered_rule ON graph.bridge_edges;
        CREATE CONSTRAINT TRIGGER bridge_edges_registered_rule
          AFTER INSERT OR UPDATE OF source_space_id,target_space_id,relation_type,weight,tenant_id
          ON graph.bridge_edges DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION graph.validate_registered_bridge();

        -- The application applies profiles on every new/upserted space.  This
        -- backfill is intentionally best-effort under tenant RLS; it never
        -- disables RLS or reaches across another tenant's graph.
        INSERT INTO graph.space_profiles(space_id,tenant_id,graph_role,cluster_key)
        SELECT id,tenant_id,
          CASE level
            WHEN 'L0' THEN 'cluster'
            WHEN 'L1' THEN 'blueprint'
            WHEN 'L2' THEN 'capability'
            WHEN 'L3' THEN 'domain'
            ELSE 'runtime_evidence'
          END,
          key
        FROM graph.spaces
        ON CONFLICT (space_id) DO NOTHING;

        SELECT set_config('app.tenant_id', id::text, true)
          FROM iam.tenants WHERE slug='local-dev';
        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-graph-routing-read',1,'active',jsonb_build_array(
          jsonb_build_object(
            'rule_id','3b75bd38-8a93-40d4-b4fa-894b08738c11',
            'effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('graph.route.read','graph.governance.read'))
          )
        )
        FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT (tenant_id,name,version) DO UPDATE
          SET status=EXCLUDED.status,rules=EXCLUDED.rules;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Multigraph governance and version history are retained; archive explicitly.")
