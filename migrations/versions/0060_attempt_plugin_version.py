"""CW3 DAG attempts record which plugin version actually ran.

``topology.execution_ledger`` (the M6 isolated chain surface) has recorded
``plugin_version`` + ``runtime_code_sha256`` since ``0041_isolated_execution``,
but the CW3 DAG surface (``control.node_attempts``) recorded only ``plugin_id``.
The two execution surfaces therefore disagreed about what an attempt fixes, and
"which version of which plugin produced this artifact" was unanswerable from the
database for a DAG run.

The values were already in hand: ``PortBoundExecutor`` carries both in its
in-memory node entry (from ``PluginExecutionResult``) — they were simply never
persisted.

Both columns are ``NOT NULL DEFAULT ''`` so every existing row stays valid, and
a node that failed before reaching the runtime honestly reports no
implementation hash rather than a fabricated one.
"""

from alembic import op

revision = "0060_attempt_plugin_version"
down_revision = "0059_align_finding_draft_input"
branch_labels = None
depends_on = None

_TABLE = "control.node_attempts"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS plugin_version      text NOT NULL DEFAULT '';
        ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS runtime_code_sha256 text NOT NULL DEFAULT '';
        """
    )


def downgrade() -> None:
    raise RuntimeError("Node attempt evidence is retained; archive explicitly.")
