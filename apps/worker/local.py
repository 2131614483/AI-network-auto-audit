"""Native PostgreSQL outbox-to-inbox dispatcher used in local development."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from uuid import UUID

import psycopg2
from psycopg2.extras import Json

from apps.worker.main import EventDispatcher, OutboxEvent, poll_once
from packages.control.executor import process_ready_task_once
from packages.demo.generator import run_demo_generation_once
from packages.knowledge.ingest import default_drop_root
from packages.knowledge.rich_media import MineruAdapter
from packages.knowledge.rich_media_service import RichMediaJobClaim, process_next_rich_media_job
from packages.observability import attach_spool_logging, configure_rotating_file_logging
from packages.plugin_topology.dag_persistence import recover_and_retry_once


def local_inbox_dispatcher(database_url: str) -> EventDispatcher:
    """Return an idempotent transport that durably receives each outbox event.

    The inbox unique constraint supplies exactly-once *handling* on top of the
    worker's at-least-once delivery.  If the process dies after the inbox commit
    but before outbox acknowledgement, retry is harmless.
    """

    def dispatch(event: OutboxEvent) -> bool:
        with psycopg2.connect(database_url) as connection:
            with connection.cursor() as cur:
                if event.tenant_id is not None:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(event.tenant_id),))
                cur.execute(
                    """INSERT INTO event.inbox
                    (outbox_event_id,tenant_id,topic,event_type,aggregate_id,payload)
                    VALUES(%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(outbox_event_id) DO NOTHING""",
                    (
                        event.id,
                        event.tenant_id,
                        event.topic,
                        event.event_type,
                        event.aggregate_id,
                        Json(event.payload),
                    ),
                )
        return True

    return dispatch


def local_rich_media_adapter(claim: RichMediaJobClaim) -> MineruAdapter:
    """Bind each durable job to exactly its staged artifact directory.

    The Worker receives no renderer-controlled path or command.  The adapter
    validates the claimed SHA256 again before it invokes the preconfigured
    local MinerU executable.
    """

    return MineruAdapter(
        output_root=(default_drop_root() / "mineru").resolve(),
        allowed_roots=(claim.path.parent.resolve(),),
        method=claim.method,
    )


def process_local_rich_media_once(
    database_url: str, *, tenant_slug: str = "local-dev", job_id: UUID | None = None
) -> bool:
    """Handle at most one persisted MinerU job; a Worker is intentionally serial."""

    result = process_next_rich_media_job(
        database_url,
        adapter_factory=local_rich_media_adapter,
        tenant_slug=tenant_slug,
        job_id=job_id,
    )
    return result is not None


def _tenant_uuid(database_url: str, tenant_slug: str) -> UUID:
    """Resolve the tenant id for the DAG recovery sweep (RLS-scoped)."""
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {tenant_slug}")
        return UUID(str(row[0]))


def write_worker_heartbeat(    database_url: str,
    *,
    worker_id: str,
    tenant_slug: str,
    loop_count: int,
    published: int = 0,
    rich: int = 0,
    tasks: int = 0,
    generated: int = 0,
) -> None:
    """Upsert the worker's durable heartbeat row (one row per worker id).

    The watchdog and the stale-lease sweeper read ``ops.worker_heartbeats`` to
    distinguish a live-but-idle worker from a dead one.  ``worker_id`` includes
    the pid, so a restarted worker gets a fresh row and its predecessor goes
    stale naturally.
    """
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ops.worker_heartbeats
                (worker_id, tenant_slug, pid, hostname, loop_count,
                 published, rich, tasks, generated, last_seen, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (worker_id) DO UPDATE SET
                tenant_slug = EXCLUDED.tenant_slug,
                pid = EXCLUDED.pid,
                hostname = EXCLUDED.hostname,
                loop_count = EXCLUDED.loop_count,
                published = EXCLUDED.published,
                rich = EXCLUDED.rich,
                tasks = EXCLUDED.tasks,
                generated = EXCLUDED.generated,
                last_seen = now(),
                updated_at = now()
            """,
            (worker_id, tenant_slug, os.getpid(), os.uname().nodename if hasattr(os, "uname") else None,
             loop_count, published, rich, tasks, generated),
        )


def run_local_forever(database_url: str, poll_seconds: float = 1.0) -> None:
    """Run outbox delivery, one task execution, and one-at-a-time local jobs.

    The queue has its own lease and does not share the outbox acknowledgement
    transaction.  One job per loop prevents a second document from contending
    for the local GPU while a parse is active.  Ready control tasks are claimed
    with a lease (recovered after expiry) and executed through the policy
    gateway; demo AIOps cycles are generated every ``generate_every`` loops
    only when ``AUDIT_NETWORK_DEMO_GENERATE=1``.
    """

    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    data_dir = Path(__file__).resolve().parents[2] / ".data"
    data_dir.mkdir(exist_ok=True)
    configure_rotating_file_logging(data_dir / "worker.log")
    # CW2 wiring: worker log records also persist into the spool so the
    # trace-locate endpoint can query real worker events after GUI close.
    attach_spool_logging(data_dir / "isolated" / "logs", producer_id="worker")
    logger = logging.getLogger("audit.worker")
    logger.info("worker started, polling every %.1fs", poll_seconds)
    dispatcher = local_inbox_dispatcher(database_url)
    tenant_slug = os.getenv("AUDIT_NETWORK_TENANT_SLUG", "local-dev")
    worker_id = os.getenv("AUDIT_NETWORK_WORKER_ID", f"worker-{os.getpid()}")
    demo_generate = os.getenv("AUDIT_NETWORK_DEMO_GENERATE", "0") == "1"
    generate_every = int(os.getenv("AUDIT_NETWORK_DEMO_GENERATE_EVERY", "120"))
    if demo_generate:
        logger.info("demo data generator enabled (every %d loops)", generate_every)
    loop_count = 0
    totals = {"published": 0, "rich": 0, "tasks": 0, "generated": 0}
    while True:
        published = poll_once(database_url, dispatch=dispatcher)
        processed = process_local_rich_media_once(database_url, tenant_slug=tenant_slug)
        task_processed = process_ready_task_once(database_url, tenant_slug=tenant_slug, worker_id=worker_id)
        generated = 0
        if demo_generate and loop_count % max(1, generate_every) == 0:
            generated = len(run_demo_generation_once(database_url, tenant_slug=tenant_slug, cycles=1))
        if loop_count % 60 == 0:
            # CW3: DAG attempt recovery sweep (stale lease -> failed, traced);
            # the retry policy itself stays explicit per node.
            try:
                recover_and_retry_once(
                    database_url, tenant_id=_tenant_uuid(database_url, tenant_slug),
                    worker_id=worker_id, stale_before_seconds=90,
                )
            except Exception:  # noqa: BLE001 - recovery must never kill the loop
                logger.exception("dag recovery sweep failed")
        totals["published"] += int(published)
        totals["rich"] += int(processed)
        totals["tasks"] += int(task_processed)
        totals["generated"] += generated
        loop_count += 1
        try:
            write_worker_heartbeat(
                database_url, worker_id=worker_id, tenant_slug=tenant_slug,
                loop_count=loop_count, **totals,
            )
        except Exception:  # noqa: BLE001 - heartbeat must never kill the loop
            logger.exception("heartbeat write failed")
        if loop_count % 60 == 0:
            logger.info(
                "heartbeat: %d loops published=%s rich=%s tasks=%s generated=%s",
                loop_count, totals["published"], totals["rich"], totals["tasks"], totals["generated"],
            )
        if not (published or processed or task_processed or generated):
            time.sleep(poll_seconds)


if __name__ == "__main__":
    # Mirror apps/api/main.py Settings: read DATABASE_URL from the environment
    # but never hard-crash when it is unset (e.g. launched directly instead of
    # through scripts/start-brain.ps1, which exports it). The fallback targets
    # the same native PostgreSQL instance the API uses.
    database_url = os.environ.get(
        "DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network"
    )
    run_local_forever(database_url)
