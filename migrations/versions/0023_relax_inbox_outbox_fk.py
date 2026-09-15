"""Remove the inbox FK that can deadlock the two-transaction local dispatcher."""

from alembic import op

revision = "0023_relax_inbox_outbox_fk"
down_revision = "0022_event_inbox_dispatcher"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE constraint_name text;
        BEGIN
          SELECT conname INTO constraint_name
          FROM pg_constraint
          WHERE conrelid = 'event.inbox'::regclass
            AND contype = 'f'
            AND confrelid = 'event.outbox'::regclass;
          IF constraint_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE event.inbox DROP CONSTRAINT %I', constraint_name);
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Inbox delivery history is retained and not cascade-dropped.")
