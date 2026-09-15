"""Minimal unified database package (Q4 / L6).

Centralises the psycopg2 connection factory used across the control plane so
every connection shares the same timeout, optional tenant context injection,
and a consistent ``connect_timeout``.  Services may keep their own connection
code for now; new code should prefer :func:`connect`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg2
import psycopg2.extensions

DEFAULT_CONNECT_TIMEOUT_SECONDS = 5


def connect(
    database_url: str,
    *,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    tenant_id: str | None = None,
) -> psycopg2.extensions.connection:
    """Open a connection with a bounded timeout and optional RLS tenant context.

    The tenant context is injected with ``set_config('app.tenant_id', ..., false)``
    so business-table queries are immediately RLS-scoped (the control plane
    enforces tenant context on every read, AGENTS.md).
    """
    connection = psycopg2.connect(database_url, connect_timeout=connect_timeout)
    if tenant_id is not None:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
    return connection


@contextmanager
def connect_ctx(
    database_url: str,
    *,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    tenant_id: str | None = None,
) -> Iterator[psycopg2.extensions.connection]:
    """Context-managed variant: always closes the connection on exit."""
    connection = connect(database_url, connect_timeout=connect_timeout, tenant_id=tenant_id)
    try:
        yield connection
    finally:
        connection.close()


def resolve_tenant_id(cur: Any, tenant_slug: str) -> str:
    """Resolve a tenant slug to its id using the given cursor (read-only)."""
    cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"tenant not found: {tenant_slug}")
    return str(row[0])
