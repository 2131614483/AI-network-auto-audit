"""Track locally staged rich files awaiting an approved extractor."""

from alembic import op

revision = "0026_library_extractors"
down_revision = "0025_control_scheduler"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge.ingest_batches ADD COLUMN IF NOT EXISTS deferred_count integer NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    raise RuntimeError("Knowledge library processing history is retained; archive explicitly.")
