"""Mark legacy dead objects deprecated (L9 remediation, R4).

ops.audit_log (migration 0005) has no writer anywhere in the repository and
ops.pending_outbox (migration 0009) is a view with no consumer.  Per the audit
report they are schema noise that misleads newcomers into thinking an audit-log
layer exists.  The remediation choice is to annotate rather than drop: the
objects are zero-row and unreferenced, but "evidence and history must not be
hard-deleted" (AGENTS.md), so the tables/views stay and carry an explicit
DEPRECATED comment plus an ownership note.
"""

from alembic import op

revision = "0051_deprecate_ops_dead_objects"
down_revision = "0050_ops_worker_heartbeat"
branch_labels = None
depends_on = None

_COMMENTS = [
    (
        "TABLE",
        "ops.audit_log",
        "DEPRECATED (2026-09-08 audit L9): created by migration 0005, no writer "
        "exists anywhere in the repository and the table is empty. Kept for "
        "history; do not use. System audit records live in policy.tool_calls / "
        "policy.decisions and ops.worker_heartbeats.",
    ),
    (
        "VIEW",
        "ops.pending_outbox",
        "DEPRECATED (2026-09-08 audit L9): created by migration 0009, no consumer "
        "exists. Kept for history; do not use. Outbox status is read from "
        "event.outbox directly.",
    ),
]


def upgrade() -> None:
    for kind, name, comment in _COMMENTS:
        op.execute(f"COMMENT ON {kind} {name} IS {_quote(comment)}")


def downgrade() -> None:
    for kind, name, _comment in _COMMENTS:
        op.execute(f"COMMENT ON {kind} {name} IS NULL")


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
