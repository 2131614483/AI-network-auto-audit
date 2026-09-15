"""Worker heartbeat ledger and alembic-version readability for supervision.

R2 of the 2026-09-08 audit remediation (O1/O5/O6/O7): the watchdog needs a
durable, tenant-free record of every running worker so it can distinguish
"worker alive but idle" from "worker died".  Each worker upserts one row per
loop; the readiness probe and the stale-lease sweeper read it.  The migration
also grants audit_app SELECT on alembic_version so the /health/ready probe can
verify the applied migration head without migrator credentials.
"""

from alembic import op

revision = "0050_ops_worker_heartbeat"
down_revision = "0049_control_lease_terminal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA IF NOT EXISTS ops;
        CREATE TABLE IF NOT EXISTS ops.worker_heartbeats (
            worker_id text PRIMARY KEY,
            tenant_slug text NOT NULL,
            pid integer,
            hostname text,
            loop_count bigint NOT NULL DEFAULT 0,
            published bigint NOT NULL DEFAULT 0,
            rich bigint NOT NULL DEFAULT 0,
            tasks bigint NOT NULL DEFAULT 0,
            generated bigint NOT NULL DEFAULT 0,
            last_seen timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS worker_heartbeats_last_seen_idx
            ON ops.worker_heartbeats (last_seen DESC);
        """
    )
    op.execute("GRANT SELECT ON TABLE public.alembic_version TO audit_app")


def downgrade() -> None:
    raise RuntimeError("Worker heartbeat ledger and supervision grants are retained; archive explicitly.")
