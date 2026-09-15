"""Allow node_revisions to record the Phase 6 merge soft-delete event.

Phase 3 shipped ``node_revisions.event_type`` limited to the four lifecycle
events.  Governed merge soft-deletes each folded source node and records a
``merged`` revision so the merge is fully auditable and reversible.  This
migration only widens the state machine; it never hard-deletes history.
"""

from alembic import op

revision = "0035_node_revisions_merged_event"
down_revision = "0034_graph_merge_arbitration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE graph.node_revisions DROP CONSTRAINT IF EXISTS node_revisions_event_type_check")
    op.execute(
        "ALTER TABLE graph.node_revisions ADD CONSTRAINT node_revisions_event_type_check "
        "CHECK (event_type IN ('update','recycled','restored','rollback','merged'))"
    )


def downgrade() -> None:
    raise RuntimeError("Phase 6 event-type widening is not reversed; archive explicitly.")