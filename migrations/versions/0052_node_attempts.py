"""CW3: per-node attempt ledger for the unified DAG execution loop.

One row per (run, node, attempt): durable lease + fencing token, port-bound
input hand-offs (every data edge), output refs, terminal state and retry
history.  Short-transaction node commits write here; outbox events reuse the
existing ``event.outbox`` (方案 7.2 短事务、恢复与幂等 / CW3 退出条件:
每条数据边和每个 attempt 均可追溯).
"""

from alembic import op

revision = "0052_node_attempts"
down_revision = "0051_deprecate_ops_dead_objects"
branch_labels = None
depends_on = None

_TABLE = "control.node_attempts"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
          attempt_id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL,
          run_id uuid NOT NULL,
          plan_key text NOT NULL,
          execution_hash text NOT NULL,
          node_instance_id text NOT NULL,
          capability text NOT NULL,
          plugin_id text NOT NULL,
          attempt_seq integer NOT NULL,
          status text NOT NULL
            CHECK (status IN ('pending','running','succeeded','failed','retry_wait','cancelled')),
          lease_token uuid,
          lease_expires_at timestamptz,
          worker_id text,
          input_bindings jsonb NOT NULL DEFAULT '{{}}',
          output_refs jsonb NOT NULL DEFAULT '{{}}',
          error_kind text,
          error_message text,
          trace_id text NOT NULL,
          idempotency_key text,
          created_at timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz,
          UNIQUE (tenant_id, run_id, node_instance_id, attempt_seq)
        )
        """
    )
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS node_attempts_tenant_isolation ON {_TABLE};
        CREATE POLICY node_attempts_tenant_isolation ON {_TABLE}
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        GRANT SELECT, INSERT, UPDATE ON {_TABLE} TO audit_app;
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS node_attempts_run_idx
          ON {_TABLE} (tenant_id, run_id, node_instance_id, attempt_seq);
        CREATE INDEX IF NOT EXISTS node_attempts_reclaim_idx
          ON {_TABLE} (tenant_id, status, lease_expires_at);
        """
    )


def downgrade() -> None:
    raise RuntimeError("Node attempt evidence is retained; archive explicitly.")
