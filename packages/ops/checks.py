"""Deterministic health checks for the 24x7 watchdog (R2 remediation).

Each check returns a :class:`Finding` with a severity so the supervisor and
alert sinks can act without parsing free text.  All queries are read-only; the
watchdog never writes business rows.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import psycopg2

Severity = Literal["ok", "warn", "crit"]

TENANT_SLUG = "local-dev"


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    severity: Severity
    message: str
    metric: float | int | None = None


def _tenant_id(cur: object) -> str:
    # mypy cannot see psycopg2 cursor types; keep the adapter local and narrow.
    cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (TENANT_SLUG,))  # type: ignore[attr-defined]
    row = cur.fetchone()  # type: ignore[attr-defined]
    if row is None:
        raise RuntimeError(f"tenant {TENANT_SLUG!r} not found")
    return str(row[0])


def check_disk_usage(data_dir: str | Path, *, warn_bytes: int = 10 * 1024**3, crit_bytes: int = 2 * 1024**3) -> Finding:
    usage = shutil.disk_usage(str(data_dir))
    free = usage.free
    if free < crit_bytes:
        return Finding("disk_usage", "crit", f"free space below critical threshold: {free / 1024**3:.1f} GiB", free)
    if free < warn_bytes:
        return Finding("disk_usage", "warn", f"free space below warning threshold: {free / 1024**3:.1f} GiB", free)
    return Finding("disk_usage", "ok", f"free space {free / 1024**3:.1f} GiB", free)


def check_outbox_backlog(
    database_url: str, *, warn_count: int = 50, crit_count: int = 500, warn_age_seconds: int = 300
) -> Finding:
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT count(*), COALESCE(max(now() - occurred_at), interval '0 seconds') "
            "FROM event.outbox WHERE published_at IS NULL"
        )
        row = cur.fetchone()
        assert row is not None
        count, max_age = row
    count = int(count)
    age_seconds = float(max_age.total_seconds()) if max_age is not None else 0.0
    if count > crit_count:
        return Finding("outbox_backlog", "crit", f"{count} unpublished events", count)
    if count > warn_count or age_seconds > warn_age_seconds:
        return Finding("outbox_backlog", "warn", f"{count} unpublished events, oldest {age_seconds:.0f}s", count)
    return Finding("outbox_backlog", "ok", f"{count} unpublished events", count)


def check_stuck_tasks(database_url: str) -> Finding:
    """Running tasks whose lease has expired (waiting for the sweeper/worker)."""
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        tenant_id = _tenant_id(cur)
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        cur.execute(
            "SELECT count(*) FROM control.task_runs "
            "WHERE tenant_id=%s AND status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < now()",
            (tenant_id,),
        )
        row = cur.fetchone()
        assert row is not None
        count = int(row[0])
    if count > 0:
        return Finding("stuck_tasks", "warn", f"{count} tasks with expired leases", count)
    return Finding("stuck_tasks", "ok", "no expired leases", 0)


def check_audit_growth(database_url: str, *, warn_rows_per_day: int = 100_000) -> Finding:
    """Policy decision volume over the last 24h (the largest append-only table)."""
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM policy.decisions WHERE decided_at > now() - interval '24 hours'"
        )
        row = cur.fetchone()
        assert row is not None
        count = int(row[0])
    if count > warn_rows_per_day:
        return Finding("audit_growth", "warn", f"{count} policy decisions in 24h", count)
    return Finding("audit_growth", "ok", f"{count} policy decisions in 24h", count)


def worker_liveness(database_url: str, *, grace_seconds: int = 90) -> list[dict[str, object]]:
    """Every known worker with its last heartbeat and staleness flag."""
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT worker_id, hostname, pid, loop_count, published, rich, tasks, generated, last_seen "
            "FROM ops.worker_heartbeats ORDER BY last_seen DESC"
        )
        rows = cur.fetchall()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=grace_seconds)
    return [
        {
            "worker_id": row[0],
            "hostname": row[1],
            "pid": row[2],
            "loop_count": row[3],
            "published": row[4],
            "rich": row[5],
            "tasks": row[6],
            "generated": row[7],
            "last_seen": row[8],
            "stale": row[8] is None or row[8] < cutoff,
        }
        for row in rows
    ]


def check_worker_liveness(database_url: str, *, grace_seconds: int = 90) -> Finding:
    workers = worker_liveness(database_url, grace_seconds=grace_seconds)
    live = [w for w in workers if not w["stale"]]
    stale = [w for w in workers if w["stale"]]
    if not workers:
        return Finding("worker_liveness", "warn", "no worker heartbeat recorded", 0)
    if stale:
        return Finding(
            "worker_liveness", "crit",
            f"{len(stale)}/{len(workers)} workers stale (live={len(live)})", len(stale),
        )
    return Finding("worker_liveness", "ok", f"{len(live)} live worker(s)", len(live))


def check_migration_head(database_url: str, expected_head: str) -> Finding:
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT version_num FROM public.alembic_version")
        row = cur.fetchone()
    actual = str(row[0]) if row is not None else "(none)"
    if actual != expected_head:
        return Finding("migration_head", "crit", f"expected {expected_head}, found {actual}")
    return Finding("migration_head", "ok", actual)


def collect_findings(
    database_url: str,
    *,
    data_dir: str | Path,
    expected_head: str,
    worker_grace_seconds: int = 90,
) -> list[Finding]:
    """Run every check and return findings in a stable order."""
    return [
        check_disk_usage(data_dir),
        check_outbox_backlog(database_url),
        check_stuck_tasks(database_url),
        check_audit_growth(database_url),
        check_worker_liveness(database_url, grace_seconds=worker_grace_seconds),
        check_migration_head(database_url, expected_head),
    ]
