"""R4 e2e: data-row -> log-location drill-down (L7 last hop).

The trace-locate endpoint answers "which data rows and which log lines belong
to this trace_id", closing the loop that the lineage endpoints already open
down to the data row.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from packages.control.scheduler import Scheduler

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"
TEST_TRACE = str(uuid4())

#: Fixed name so repeated runs update one row instead of adding another.
_POLICY_SET = "e2e-trace-locate-read"


def _grant_trace_read(tenant_id: UUID) -> None:
    """Provision ``observability.trace.read`` for this test only.

    This suite used to pass on grants leaked by other modules (``cw0-trace-*`` /
    ``cw2-trace-*`` sets, one accumulated per run, each granting exactly this
    capability); it started returning 409 as soon as that ambient ALLOW was
    swept out of the shared test database.  ``conftest``'s policy guard
    deactivates the set afterwards, so nothing is left behind.
    """
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) "
            "VALUES(%s,%s,1,'active',%s) "
            "ON CONFLICT (tenant_id,name,version) DO UPDATE "
            "SET status=EXCLUDED.status, rules=EXCLUDED.rules",
            (
                tenant_id,
                _POLICY_SET,
                Json([
                    {
                        "rule_id": str(uuid4()),
                        "effect": "allow",
                        "match": {"capabilities": ["observability.trace.read"]},
                    }
                ]),
            ),
        )


@pytest.fixture(autouse=True)
def _trace_read_grant(tenant_id: UUID):
    _grant_trace_read(tenant_id)
    yield


@pytest.fixture(scope="module")
def tenant_id() -> UUID:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


@pytest.fixture()
def client(tenant_id: UUID, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """API instance pinned to the test database so the endpoint reads test rows."""
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    from apps.api.main import create_app

    return TestClient(create_app())


@pytest.fixture(scope="module")
def seeded_trace_id(tenant_id: UUID) -> str:
    scheduler = Scheduler(TEST_DB, "local-dev")
    mission_id = scheduler.create_mission(
        "r4-trace-locate", "e2e trace locate", "audit", idempotency_key="demo-mission-r4-trace-locate"
    )
    run_id = scheduler.start_run(
        mission_id, "demo-dag", "1.0.0", {"input": "locate"}, f"r4-locate-{uuid4().hex}", trace_id=UUID(TEST_TRACE)
    )
    assert run_id is not None
    return TEST_TRACE


def test_trace_locate_returns_data_rows_and_log_locations(
    client: TestClient, tenant_id: UUID, seeded_trace_id: str,
) -> None:
    head = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    response = client.get(f"/api/v1/observability/trace/{seeded_trace_id}", headers=head)
    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == seeded_trace_id
    kinds = {row["kind"] for row in body["data_rows"]}
    assert "task_run" in kinds or "workflow_run" in kinds
    assert body["log_locations"], "must offer at least one log file + query"
    for location in body["log_locations"]:
        assert location["file"].endswith(".log")
        assert seeded_trace_id in location["query"]
    assert body["trace_id"] == seeded_trace_id
