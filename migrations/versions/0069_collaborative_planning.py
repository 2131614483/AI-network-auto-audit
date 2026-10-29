"""Durable, tenant-scoped collaborative planning; no execution grants."""

from alembic import op

revision = "0069_collaborative_planning"
down_revision = "0068_connectivity_read_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS topology.planning_sessions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          trace_id uuid NOT NULL,
          idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
          request_hash text NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
          request_json jsonb NOT NULL CHECK (jsonb_typeof(request_json)='object'),
          status text NOT NULL DEFAULT 'queued' CHECK (status IN (
            'queued','running','accepted','blocked_input','blocked_capability',
            'blocked_policy','budget_exhausted','stalled','failed','cancelled'
          )),
          checkpoint_json jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(checkpoint_json)='object'),
          result_json jsonb CHECK (result_json IS NULL OR jsonb_typeof(result_json)='object'),
          decision_id uuid NOT NULL REFERENCES policy.decisions(id),
          worker_id text,
          lease_token uuid,
          lease_expires_at timestamptz,
          claim_count integer NOT NULL DEFAULT 0 CHECK (claim_count >= 0),
          next_event_seq bigint NOT NULL DEFAULT 0 CHECK (next_event_seq >= 0),
          started_at timestamptz,
          finished_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(tenant_id,idempotency_key),
          UNIQUE(tenant_id,id)
        );
        CREATE TABLE IF NOT EXISTS topology.planning_session_events (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
          session_id uuid NOT NULL,
          trace_id uuid NOT NULL,
          event_seq bigint NOT NULL CHECK (event_seq > 0),
          event_key text NOT NULL CHECK (length(event_key) BETWEEN 1 AND 200),
          event_type text NOT NULL CHECK (event_type ~ '^[a-z][a-z0-9_.-]{0,63}$'),
          round_no integer NOT NULL CHECK (round_no BETWEEN 1 AND 8),
          payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
          payload_hash text NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
          decision_id uuid NOT NULL REFERENCES policy.decisions(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY(tenant_id,session_id) REFERENCES topology.planning_sessions(tenant_id,id),
          UNIQUE(tenant_id,session_id,event_key),
          UNIQUE(tenant_id,session_id,event_seq)
        );
        CREATE INDEX IF NOT EXISTS planning_sessions_pending_idx
          ON topology.planning_sessions(tenant_id,status,lease_expires_at,created_at)
          WHERE status IN ('queued','running');
        CREATE INDEX IF NOT EXISTS planning_session_events_trace_idx
          ON topology.planning_session_events(tenant_id,trace_id,event_seq);
        REVOKE ALL ON topology.planning_sessions,topology.planning_session_events FROM PUBLIC;
        GRANT SELECT,INSERT,UPDATE ON topology.planning_sessions TO audit_app;
        REVOKE DELETE ON topology.planning_sessions FROM audit_app;
        GRANT SELECT,INSERT ON topology.planning_session_events TO audit_app;
        REVOKE UPDATE,DELETE ON topology.planning_session_events FROM audit_app;
        """
    )
    for table in ("planning_sessions", "planning_session_events"):
        op.execute(f"ALTER TABLE topology.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE topology.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON topology.{table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON topology.{table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
    op.execute(
        """
        SELECT set_config('app.tenant_id', id::text, true)
          FROM iam.tenants WHERE slug='local-dev';
        INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
        SELECT id,'local-collaborative-planning',1,'inactive',jsonb_build_array(
          jsonb_build_object('rule_id','c1000001-0000-4000-8000-000000000001','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.collaborative.plan'),
              'risk_classes',jsonb_build_array('low'),'side_effects',jsonb_build_array('write_data'))),
          jsonb_build_object('rule_id','c1000001-0000-4000-8000-000000000002','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.collaborative.read'),
              'risk_classes',jsonb_build_array('read_only'),'side_effects',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','c1000001-0000-4000-8000-000000000003','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.collaborative.graph.read'),
              'risk_classes',jsonb_build_array('read_only'),'side_effects',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','c1000001-0000-4000-8000-000000000004','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.collaborative.history.read'),
              'risk_classes',jsonb_build_array('read_only'),'side_effects',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','c1000001-0000-4000-8000-000000000005','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.collaborative.knowledge.read'),
              'risk_classes',jsonb_build_array('read_only'),'side_effects',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','c1000001-0000-4000-8000-000000000006','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.collaborative.model.call'),
              'risk_classes',jsonb_build_array('low'),'side_effects',jsonb_build_array('read_only'))),
          jsonb_build_object('rule_id','c1000001-0000-4000-8000-000000000007','effect','allow',
            'match',jsonb_build_object('capabilities',jsonb_build_array('topology.collaborative.input.read'),
              'risk_classes',jsonb_build_array('read_only'),'side_effects',jsonb_build_array('read_only')))
        ) FROM iam.tenants WHERE slug='local-dev'
        ON CONFLICT(tenant_id,name,version) DO NOTHING;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Collaborative planning history is retained; archive explicitly.")
