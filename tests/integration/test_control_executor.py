"""R1 contracts: task leases, stale reclaim, and terminal-state transitions.

Every claim carries a durable lease so a crashed worker cannot leave a task
stuck in 'running'; runs and missions advance pending -> running -> terminal,
and a failed node cascades its dependents to cancelled so the run can reach a
terminal state with real rollback evidence.
"""

from __future__ import annotations

import os
import time
from typing import Any
from uuid import UUID, uuid4

import psycopg2
import pytest
from psycopg2.extras import Json

from packages.control.executor import process_ready_task_once
from packages.control.scheduler import PluginExecutor, Scheduler

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        return UUID(str(cur.fetchone()[0]))


def _connect(tenant_id: UUID) -> Any:
    """Connection with the session tenant set so RLS-FORCE rows are visible."""
    connection = psycopg2.connect(DB)
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
    return connection


def _allow(tenant_id: UUID, capabilities: list[str]) -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,999,'active',%s)",
            (tenant_id, f"executor-{uuid4().hex}", Json([
                {"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": capabilities}}
            ])),
        )


def _make_dag(scheduler: Scheduler, tenant_id: UUID, suffix: str) -> tuple[UUID, UUID, list[str]]:
    capabilities = [f"exec.{suffix}.a", f"exec.{suffix}.b", f"exec.{suffix}.c"]
    _allow(tenant_id, capabilities)
    scheduler.create_workflow(
        f"exec-{suffix}", "Executor DAG", "1.0.0",
        {"nodes": [
            {"key": "a", "capability": capabilities[0]},
            {"key": "b", "capability": capabilities[1], "depends_on": ["a"]},
            {"key": "c", "capability": capabilities[2], "depends_on": ["b"]},
        ]},
    )
    mission = scheduler.create_mission(f"Executor {suffix}", "run", "audit")
    run = scheduler.start_run(mission, f"exec-{suffix}", "1.0.0", {"input": 1}, f"exec-run-{uuid4().hex}")
    return mission, run, capabilities


def _states(connection: Any, table: str, row_id: UUID) -> str:
    with connection.cursor() as cur:
        cur.execute(f"SELECT status FROM control.{table} WHERE id=%s", (row_id,))
        return str(cur.fetchone()[0])


def test_claim_sets_lease_and_stale_claim_is_reclaimed() -> None:
    scheduler = Scheduler(DB)
    tenant_id = _tenant_id()
    mission, run, _caps = _make_dag(scheduler, tenant_id, f"lease-{uuid4().hex[:8]}")
    ready = scheduler.ready_tasks(limit=1, workflow_run_id=run)
    assert ready
    first = scheduler.claim_task(ready[0].id, "agent", "model", f"agent-{uuid4().hex}", lease_seconds=30)
    with _connect(tenant_id) as connection, connection.cursor() as cur:
        cur.execute("SELECT lease_token, lease_expires_at, status FROM control.task_runs WHERE id=%s", (ready[0].id,))
        token, expires, status = cur.fetchone()
        assert token is not None and expires is not None
        assert status == "running"
        assert _states(connection, "workflow_runs", run) == "running"
        assert _states(connection, "missions", mission) == "running"
    # The same task cannot be claimed again while the lease is live.
    with pytest.raises(ValueError, match="not claimable"):
        scheduler.claim_task(ready[0].id, "agent", "model", f"agent-{uuid4().hex}", lease_seconds=30)
    # Force expiry, then a second worker reclaims and the orphaned agent is cancelled.
    with _connect(tenant_id) as connection, connection.cursor() as cur:
        cur.execute("UPDATE control.task_runs SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (ready[0].id,))
    reclaim = scheduler.claim_task(ready[0].id, "agent", "model", f"agent-{uuid4().hex}", lease_seconds=30, worker_id="w2")
    with _connect(tenant_id) as connection, connection.cursor() as cur:
        cur.execute("SELECT status FROM control.agent_runs WHERE id=%s", (first.id,))
        assert cur.fetchone()[0] == "cancelled"
        assert _states(connection, "missions", mission) == "running"
    scheduler.fail_agent(reclaim.id, "reclaim-abandoned")


def test_completed_run_finalizes_workflow_and_mission() -> None:
    scheduler = Scheduler(DB)
    tenant_id = _tenant_id()
    mission, run, caps = _make_dag(scheduler, tenant_id, f"ok-{uuid4().hex[:8]}")
    executor = PluginExecutor(scheduler)
    executor.register(caps[0], lambda payload: {"a": True})
    executor.register(caps[1], lambda payload: {"b": True})
    executor.register(caps[2], lambda payload: {"c": True})
    while True:
        ready = scheduler.ready_tasks(limit=1, workflow_run_id=run)
        if not ready:
            break
        claim = scheduler.claim_task(ready[0].id, "agent", "model", f"agent-{uuid4().hex}")
        executor.execute(claim.id)
    with _connect(tenant_id) as connection:
        assert _states(connection, "workflow_runs", run) == "completed"
        assert _states(connection, "missions", mission) == "completed"
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM control.task_runs WHERE workflow_run_id=%s AND status='completed'", (run,))
            assert cur.fetchone()[0] == 3


def test_failed_node_cascades_dependents_and_terminates_mission() -> None:
    scheduler = Scheduler(DB)
    tenant_id = _tenant_id()
    mission, run, _caps = _make_dag(scheduler, tenant_id, f"fail-{uuid4().hex[:8]}")
    first = scheduler.ready_tasks(limit=1, workflow_run_id=run)
    assert first
    claim = scheduler.claim_task(first[0].id, "agent", "model", f"agent-{uuid4().hex}")
    scheduler.fail_agent(claim.id, "simulated outage")
    with _connect(tenant_id) as connection:
        assert _states(connection, "workflow_runs", run) == "failed"
        assert _states(connection, "missions", mission) == "failed"
        with connection.cursor() as cur:
            cur.execute(
                "SELECT status FROM control.task_runs WHERE workflow_run_id=%s",
                (run,),
            )
            assert sorted(row[0] for row in cur.fetchall()) == ["cancelled", "cancelled", "failed"]


def test_process_ready_task_once_records_policy_decision() -> None:
    scheduler = Scheduler(DB)
    tenant_id = _tenant_id()
    mission, run, caps = _make_dag(scheduler, tenant_id, f"exec-{uuid4().hex[:8]}")
    before = time.time()
    handlers = {caps[0]: lambda payload: {"a": True}, caps[1]: lambda payload: {"b": True}, caps[2]: lambda payload: {"c": True}}
    assert process_ready_task_once(DB, worker_id="test-worker", handlers=handlers, workflow_run_id=run) is True
    with _connect(tenant_id) as connection, connection.cursor() as cur:
        # The executor persisted an immutable policy decision before running.
        cur.execute(
            "SELECT count(*) FROM policy.tool_calls WHERE tenant_id=%s AND requested_at > to_timestamp(%s)",
            (tenant_id, before),
        )
        assert cur.fetchone()[0] >= 1
        cur.execute(
            "SELECT status FROM control.task_runs WHERE workflow_run_id=%s ORDER BY created_at, id",
            (run,),
        )
        statuses = [row[0] for row in cur.fetchall()]
    # Exactly one task completed (root node); the others wait for readiness.
    assert statuses.count("completed") == 1
    assert statuses.count("pending") + statuses.count("ready") == 2
    assert process_ready_task_once(DB, worker_id="test-worker", handlers=handlers, workflow_run_id=run) is True  # node b now ready
    with _connect(tenant_id) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT status FROM control.task_runs WHERE workflow_run_id=%s",
            (run,),
        )
        assert [row[0] for row in cur.fetchall()].count("completed") == 2
