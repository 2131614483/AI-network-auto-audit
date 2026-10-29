"""Dedicated plan-only queue consumer; never dispatches a plugin execution."""

from __future__ import annotations

import logging
import os
import time
from uuid import UUID

import psycopg2

from packages.ai_planner.planning_service import CollaborativePlanningService, PlanningAccessDenied

logger = logging.getLogger("audit.collaborative_worker")


def process_collaborative_once(
    database_url: str, *, tenant_slug: str = "local-dev", worker_id: str | None = None,
) -> bool:
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError("planning worker tenant not found")
        tenant_id = UUID(str(row[0]))
    result = CollaborativePlanningService(database_url).process_next(
        tenant_id, worker_id=worker_id or f"collaborative-{os.getpid()}",
    )
    return result is not None


def run_collaborative_forever(database_url: str, tenant_slug: str = "local-dev") -> None:
    while True:
        try:
            worked = process_collaborative_once(database_url, tenant_slug=tenant_slug)
            if not worked:
                time.sleep(3)
        except PlanningAccessDenied:
            logger.warning("collaborative planning worker lacks a required policy grant")
            time.sleep(30)
        except Exception:
            logger.exception("collaborative planning iteration failed; committed checkpoints retained")
            time.sleep(10)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required; run explicit migrations before this worker")
    run_collaborative_forever(url, os.getenv("AUDIT_NETWORK_TENANT_SLUG", "local-dev"))
