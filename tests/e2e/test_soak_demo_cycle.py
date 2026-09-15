"""R4 e2e: soak — repeated demo AIOps cycles stay stable, idempotent and
consistent across many unattended rounds (O10 acceptance, test database).

Runs a small number of full cycles (configurable via AUDIT_NETWORK_SOAK_ROUNDS,
default 3) so the whole suite stays fast while still exercising the loop that
runs 24x7 in production.
"""

from __future__ import annotations

import os
from uuid import UUID

import psycopg2
import pytest

from packages.demo.generator import generate_aiops_cycle

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"
ROUNDS = int(os.getenv("AUDIT_NETWORK_SOAK_ROUNDS", "3"))


@pytest.fixture(scope="module")
def tenant_id() -> UUID:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


def _count(tenant_id: UUID, table: str, where: str) -> int:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(f"SELECT count(*) FROM {table} WHERE tenant_id=%s AND {where}", (tenant_id,))
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


def test_aiops_cycles_soak_stable(tenant_id: UUID) -> None:
    assert ROUNDS >= 1
    incident_before = _count(tenant_id, "aiops.incidents", "true")
    execution_before = _count(tenant_id, "aiops.executions", "true")

    for _ in range(ROUNDS):
        produced = generate_aiops_cycle(TEST_DB, tenant_slug="local-dev")
        assert produced, "every soak round must produce a full AIOps cycle"
        # Every cycle creates at least one fresh incident + execution; the
        # exact count may vary with the random rollback branch, so assert a
        # lower bound and rely on the per-cycle closed-loop check below.
        assert _count(tenant_id, "aiops.incidents", "true") >= incident_before + 1
        assert _count(tenant_id, "aiops.executions", "true") >= execution_before + 1
        # The execution produced THIS round must reach a closed-loop status.
        with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.execute(
                "SELECT status FROM aiops.executions WHERE tenant_id=%s AND id=%s",
                (tenant_id, produced["execution_id"]),
            )
            row = cur.fetchone()
            assert row is not None
            status = str(row[0])
            assert status in {"verified", "rolled_back"}, f"cycle execution not closed: {status}"
