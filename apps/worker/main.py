from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg2


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """The immutable event envelope handed to a dispatcher."""

    id: int
    tenant_id: Any
    topic: str
    event_type: str
    aggregate_id: Any
    payload: dict[str, Any]
    occurred_at: datetime


EventDispatcher = Callable[[OutboxEvent], bool | None]


def poll_once(
    database_url: str, limit: int = 50, dispatch: EventDispatcher | None = None
) -> int:
    """Dispatch and acknowledge a bounded batch of outbox events.

    Rows are locked with ``SKIP LOCKED`` so multiple workers can safely poll in
    parallel.  An event is marked published *only after* its dispatcher returns
    successfully; a false return or exception leaves it available for a later
    retry.  Dispatchers must make their side effects idempotent using ``event.id``.

    ``dispatch=None`` is a safe inspection mode: events are claimed for the
    transaction but never acknowledged.  Production callers must pass a real
    dispatcher; this prevents a process started without a plugin transport from
    silently losing work.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT id, tenant_id, topic, event_type, aggregate_id, payload, occurred_at
                FROM event.outbox
                WHERE published_at IS NULL
                ORDER BY occurred_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            published = 0
            for row in rows:
                event = OutboxEvent(
                    id=int(row[0]),
                    tenant_id=row[1],
                    topic=str(row[2]),
                    event_type=str(row[3]),
                    aggregate_id=row[4],
                    payload=dict(row[5]),
                    occurred_at=row[6],
                )
                try:
                    delivered = False if dispatch is None else dispatch(event)
                except Exception:
                    # The transaction remains healthy and the event is not
                    # acknowledged.  The next poll can retry it.
                    delivered = False
                if not delivered:
                    continue
                cur.execute(
                    "UPDATE event.outbox SET published_at=now() WHERE id=%s AND published_at IS NULL",
                    (event.id,),
                )
                published += cur.rowcount
            return published


def run_forever(
    database_url: str, poll_seconds: float = 1.0, dispatch: EventDispatcher | None = None
) -> None:
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    if dispatch is None:
        raise ValueError("run_forever requires a real event dispatcher")
    while True:
        poll_once(database_url, dispatch=dispatch)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(
        "No event dispatcher is configured. Import run_forever() with a plugin transport."
    )
