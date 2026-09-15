"""Experience L2 light-solidification: link accepted suggestions to governance.

Design §5.5 exit 1 ("light solidification").  When a human accepts a
design-external relation suggestion it is recorded as a governance ChangeSet
(change_type='experience', target_kind='experience.relation') and published
through the existing knowledge release lifecycle.  These three columns link
the (never-deleted) suggestion back to its governance records so the graph can
distinguish a merely *accepted* suggestion from a *released/confirmed* relation.

No plugin manifest and no executor is touched.  The observation/rollup tables
and the suggestion state machine are unchanged.  RLS is already ENABLE+FORCE on
the table and is inherited by the new columns; the app role already holds
SELECT/INSERT/UPDATE.

Downgrade is refused (evidence/governance links are retained, same stance as
0055) instead of dropping columns.
"""

from alembic import op

revision = "0057_experience_solidify"
down_revision = "0056_experience_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE experience.relation_suggestions
          ADD COLUMN IF NOT EXISTS changeset_id uuid REFERENCES knowledge.change_sets(id),
          ADD COLUMN IF NOT EXISTS release_id uuid REFERENCES knowledge.releases(id),
          ADD COLUMN IF NOT EXISTS released_at timestamptz
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS relation_suggestions_released_idx
          ON experience.relation_suggestions (tenant_id)
          WHERE released_at IS NOT NULL
        """
    )


def downgrade() -> None:
    raise RuntimeError("Experience solidification links are retained; archive explicitly.")
