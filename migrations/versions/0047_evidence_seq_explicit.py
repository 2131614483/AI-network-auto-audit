"""Plugin Topology M9: make evidence chain ``seq`` explicitly managed.

The anchor table was originally created with ``seq GENERATED ALWAYS AS
IDENTITY`` (table-global), but the evidence chain numbers each anchor with a
*chain-local* position (``evidence://<tenant>/full`` starting at 1) so the
application can replay the predecessor links during verification.  A
table-global identity lets the store and the replay disagree, dissolving the
chain-local invariant ("the first anchor of an empty chain has seq 1 and is
seeded to the chain key").  This migration drops the identity so ``seq``
becomes a plain ``bigint`` written explicitly by ``anchor_evidence``; existing
rows keep their values and remain internally consistent.
"""

from __future__ import annotations

from alembic import op

revision = "0047_evidence_seq_explicit"
down_revision = "0046_evidence_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: 0046 created the table with a plain bigint ``seq`` (no
    # identity), so on a fresh database there is nothing to drop.  Databases
    # that carried the earlier identity definition (upgrade path through a
    # previous 0046 shape) still get the identity removed.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='topology' AND table_name='evidence_chain_anchors'
              AND column_name='seq' AND is_identity='YES'
          ) THEN
            ALTER TABLE topology.evidence_chain_anchors ALTER COLUMN seq DROP IDENTITY;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # The chain-local seq no longer maps to a table-global identity; restoring
    # it would only be valid for an empty table, so this is intentionally a no-op
    # that documents the semantic change rather than pretending to revert data.
    pass