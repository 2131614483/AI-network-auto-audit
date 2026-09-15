"""Plugin Topology M6: exclusive running runs (DB-level 409 backstop).

``begin_run`` checks for a ``running`` run inside its own transaction, but two
workers could race past that check.  This partial unique index is the hard
backstop: only one non-terminal run row may exist per (tenant, chain), so a
second concurrent ``running`` insert fails with a unique violation that the
service maps to a 409 conflict.
"""

from __future__ import annotations

from alembic import op

revision = "0043_chain_run_exclusive_running"
down_revision = "0042_chain_execution_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS topology_execution_runs_one_running
          ON topology.execution_runs (tenant_id, chain_id)
          WHERE status = 'running';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS topology_execution_runs_one_running")