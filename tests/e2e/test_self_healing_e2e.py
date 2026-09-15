"""R4 e2e: self-healing — a crashed worker's lease is recovered, its orphaned
agent run cancelled, and the task is re-claimed and driven to a terminal state
through the real scheduler path (O10 / O3 acceptance on the test database).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg2
import pytest
from psycopg2.extras import Json

from packages.control.executor import DEFAULT_DEMO_HANDLERS
from packages.control.scheduler import PluginExecutor, Scheduler

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"

#: Fixed name so repeated runs update one row instead of adding another.
_POLICY_SET = "e2e-self-heal-scheduler"
_DAG_CAPABILITIES = ["scheduler.a", "scheduler.b", "scheduler.c"]


def _grant_dag_capabilities(tenant_id: UUID) -> None:
    """Provision the demo DAG's capabilities for this test only.

    This suite used to pass on grants leaked by other modules (``scheduler-*``
    sets, one accumulated per run); it failed as soon as that ambient ALLOW was
    swept out of the shared test database.  A test must provision what it
    asserts on.  ``conftest``'s policy guard deactivates this set afterwards, so
    it never leaves an ambient grant behind.
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
                        "match": {"capabilities": _DAG_CAPABILITIES},
                    }
                ]),
            ),
        )


@pytest.fixture(autouse=True)
def _dag_capability_grant(tenant_id: UUID):
    _grant_dag_capabilities(tenant_id)
    yield


@pytest.fixture(scope="module")
def tenant_id() -> UUID:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


@pytest.fixture(scope="module")
def run_id(tenant_id: UUID) -> UUID:
    """Create an idempotent test mission + workflow run with a ready task."""
    scheduler = Scheduler(TEST_DB, "local-dev")
    mission_id = scheduler.create_mission(
        "r4-self-heal", "e2e self-healing", "audit", idempotency_key="demo-mission-r4-self-heal"
    )
    run_key = f"r4-self-heal-{uuid4().hex}"
    created = scheduler.start_run(
        mission_id,
        "demo-dag",
        "1.0.0",
        {"input": "self-heal"},
        run_key,
        trace_id=uuid4(),
    )
    assert created is not None
    return UUID(str(created))

def _expire_lease(tenant_id: UUID, task_run_id: UUID) -> None:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "UPDATE control.task_runs SET lease_expires_at=%s WHERE id=%s",
            (datetime.now(timezone.utc) - timedelta(seconds=5), task_run_id),
        )


def _task_status(tenant_id: UUID, task_run_id: UUID) -> str:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute("SELECT status FROM control.task_runs WHERE id=%s", (task_run_id,))
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


def _make_retryable(tenant_id: UUID, task_run_id: UUID) -> None:
    """Give the task retry budget so crash recovery returns it to ready."""
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute("UPDATE control.task_runs SET max_attempts=2 WHERE id=%s", (task_run_id,))


def _agent_run_status(tenant_id: UUID, agent_run_id: UUID) -> str:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute("SELECT status FROM control.agent_runs WHERE id=%s", (agent_run_id,))
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


def test_crashed_worker_lease_is_recovered_and_task_redrives_to_completed(
    tenant_id: UUID, run_id: UUID
) -> None:
    scheduler = Scheduler(TEST_DB, "local-dev")
    ready = scheduler.ready_tasks(limit=1, workflow_run_id=run_id)
    assert ready, "start_run must leave a ready task"
    task = ready[0]
    _make_retryable(tenant_id, task.id)

    first_claim = scheduler.claim_task(
        task.id, "scheduler-worker", "local-demo", f"agent-{uuid4().hex}",
        lease_seconds=30, worker_id="r4-crashed-worker",
    )
    _expire_lease(tenant_id, task.id)

    # The worker never finishes; a later watchdog pass recovers the lease.
    result = scheduler.recover_stale_leases(worker_grace_seconds=90)
    assert result["recovered"] == 1
    assert _task_status(tenant_id, task.id) == "ready"
    assert _agent_run_status(tenant_id, first_claim.id) == "cancelled"

    # A fresh worker re-claims and drives the task to completion.
    second_claim = scheduler.claim_task(
        task.id, "scheduler-worker", "local-demo", f"agent-{uuid4().hex}",
        lease_seconds=30, worker_id="r4-recovery-worker",
    )
    executor = PluginExecutor(scheduler)
    for capability, handler in DEFAULT_DEMO_HANDLERS.items():
        executor.register(capability, handler)
    executor.execute(second_claim.id)
    assert _task_status(tenant_id, task.id) == "completed"
