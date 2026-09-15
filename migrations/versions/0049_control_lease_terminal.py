"""Task-run leases and terminal-state checks for 24x7 unattended execution.

R1 of the 2026-09-08 audit remediation: the scheduler previously claimed tasks
without a lease, so a worker crash left task_runs stuck in 'running' forever.
This migration adds a durable lease (token + expiry + worker id) so a stale
claim can be reclaimed after expiry, and documents the terminal-state
vocabulary for workflow_runs/missions with NOT VALID checks (existing rows are
left untouched; validation is a separate, explicit step).
"""

from alembic import op

revision = "0049_control_lease_terminal"
down_revision = "0048_graph_driven_planning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE control.task_runs
          ADD COLUMN IF NOT EXISTS lease_token uuid,
          ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
          ADD COLUMN IF NOT EXISTS worker_id text;
        CREATE INDEX IF NOT EXISTS task_runs_reclaim_idx
          ON control.task_runs (tenant_id, status, lease_expires_at);
        """
    )
    op.execute(
        "ALTER TABLE control.workflow_runs "
        "ADD CONSTRAINT workflow_runs_status_check "
        "CHECK (status IN ('pending','running','completed','failed','cancelled')) NOT VALID"
    )
    op.execute(
        "ALTER TABLE control.missions "
        "ADD CONSTRAINT missions_status_check "
        "CHECK (status IN ('planned','running','completed','failed','cancelled')) NOT VALID"
    )
    op.execute(
        "ALTER TABLE control.task_runs "
        "ADD CONSTRAINT task_runs_status_check "
        "CHECK (status IN ('pending','ready','running','completed','failed','cancelled')) NOT VALID"
    )


def downgrade() -> None:
    raise RuntimeError("Lease and terminal-state columns are retained; archive explicitly.")
