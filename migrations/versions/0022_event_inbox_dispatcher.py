"""Add a local idempotent inbox for the native outbox dispatcher."""

from alembic import op

revision = "0022_event_inbox_dispatcher"
down_revision = "0021_knowledge_release_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS event.inbox (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          -- No FK here: dispatch runs in a second transaction while the
          -- worker holds an outbox row lock.  A FK would wait on that lock and
          -- deadlock the outbox-to-inbox handoff.  `outbox_event_id` is an
          -- immutable idempotency key, not a mutable business reference.
          outbox_event_id bigint NOT NULL UNIQUE,
          tenant_id uuid REFERENCES iam.tenants(id),
          topic text NOT NULL,
          event_type text NOT NULL,
          aggregate_id uuid,
          payload jsonb NOT NULL,
          received_at timestamptz NOT NULL DEFAULT now(),
          handler_status text NOT NULL DEFAULT 'received'
        );
        CREATE INDEX IF NOT EXISTS event_inbox_unhandled_idx
          ON event.inbox(tenant_id, received_at) WHERE handler_status='received';
        ALTER TABLE event.inbox ENABLE ROW LEVEL SECURITY;
        ALTER TABLE event.inbox FORCE ROW LEVEL SECURITY;
        CREATE POLICY inbox_tenant_isolation ON event.inbox
          USING (tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        GRANT SELECT, INSERT, UPDATE ON event.inbox TO audit_app;
        GRANT USAGE, SELECT ON SEQUENCE event.inbox_id_seq TO audit_app;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Inbox delivery history is retained and not cascade-dropped.")
