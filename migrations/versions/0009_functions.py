"""Create safe outbox helper functions."""
from alembic import op

revision = "0009_functions"
down_revision = "0008_seed_local"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION event.enqueue_outbox(
      p_tenant_id uuid, p_topic text, p_event_type text, p_aggregate_id uuid, p_payload jsonb
    ) RETURNS bigint LANGUAGE SQL AS $$
      INSERT INTO event.outbox(tenant_id, topic, event_type, aggregate_id, payload)
      VALUES (p_tenant_id, p_topic, p_event_type, p_aggregate_id, p_payload)
      RETURNING id
    $$
    """)
    op.execute("""
    CREATE OR REPLACE VIEW ops.pending_outbox AS
      SELECT id, tenant_id, topic, event_type, aggregate_id, payload, occurred_at
      FROM event.outbox WHERE published_at IS NULL ORDER BY occurred_at, id
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ops.pending_outbox")
    op.execute("DROP FUNCTION IF EXISTS event.enqueue_outbox(uuid,text,text,uuid,jsonb)")
