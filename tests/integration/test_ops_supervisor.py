"""R2 supervision: lease renewal, stale-lease sweeper, worker heartbeat, checks.

Each test uses a private DAG and scoped ``ready_tasks`` so cross-run claims are
impossible.  Direct SQL updates force lease expiry where a real 30s wait would
be unacceptable.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID, uuid4

import psycopg2
import pytest
from psycopg2.extras import Json

from apps.worker.local import write_worker_heartbeat
from packages.control.scheduler import Scheduler
from packages.ops.checks import collect_findings, worker_liveness
from packages.ops.supervisor import EXPECTED_MIGRATION_HEAD

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


def _connect(tenant_id: UUID) -> Any:
    connection = psycopg2.connect(DB)
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
    return connection


def _allow(tenant_id: UUID, capabilities: list[str]) -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,999,'active',%s)",
            (tenant_id, f"ops-policy-{uuid4().hex}", Json([
                {"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": capabilities}}
            ])),
        )


def _make_dag(scheduler: Scheduler, tenant_id: UUID, suffix: str, *, max_attempts: int = 1) -> tuple[UUID, UUID]:
    capabilities = [f"ops.{suffix}.a", f"ops.{suffix}.b", f"ops.{suffix}.c"]
    _allow(tenant_id, capabilities)
    scheduler.create_workflow(
        f"ops-{suffix}", "Ops DAG", "1.0.0",
        {"nodes": [
            {"key": "a", "capability": capabilities[0], "max_attempts": max_attempts},
            {"key": "b", "capability": capabilities[1], "depends_on": ["a"], "max_attempts": max_attempts},
            {"key": "c", "capability": capabilities[2], "depends_on": ["b"], "max_attempts": max_attempts},
        ]},
    )
    mission = scheduler.create_mission(f"Ops {suffix}", "run", "audit")
    run = scheduler.start_run(mission, f"ops-{suffix}", "1.0.0", {"input": 1}, f"ops-run-{uuid4().hex}")
    return mission, run


def _expire_lease(tenant_id: UUID, task_run_id: UUID) -> None:
    with _connect(tenant_id) as connection, connection.cursor() as cur:
        cur.execute(
            "UPDATE control.task_runs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
            (task_run_id,),
        )


def _task_status(tenant_id: UUID, task_run_id: UUID) -> str:
    with _connect(tenant_id) as connection, connection.cursor() as cur:
        cur.execute("SELECT status FROM control.task_runs WHERE id=%s", (task_run_id,))
        return str(cur.fetchone()[0])


def test_renew_lease_extends_expiry_and_validates_owner() -> None:
    scheduler = Scheduler(DB)
    tenant_id = _tenant_id()
    _mission, run = _make_dag(scheduler, tenant_id, f"renew-{uuid4().hex[:8]}")
    ready = scheduler.ready_tasks(limit=1, workflow_run_id=run)
    assert ready
    agent = scheduler.claim_task(ready[0].id, "agent", "model", f"agent-{uuid4().hex}", lease_seconds=30, worker_id="w1")
    renewed = scheduler.renew_lease(agent.id, lease_seconds=300, worker_id="w1")
    with _connect(tenant_id) as connection, connection.cursor() as cur:
        cur.execute("SELECT lease_expires_at FROM control.task_runs WHERE id=%s", (ready[0].id,))
        stored = cur.fetchone()[0]
    assert renewed == stored
    assert stored is not None and stored.tzinfo is not None
    # A different worker cannot renew someone else's lease.
    with pytest.raises(PermissionError, match="owned by another worker"):
        scheduler.renew_lease(agent.id, lease_seconds=300, worker_id="w2")
    scheduler.fail_agent(agent.id, "renew-test-done")


def test_recover_stale_leases_returns_task_to_ready_when_worker_silent() -> None:
    scheduler = Scheduler(DB)
    tenant_id = _tenant_id()
    _mission, run = _make_dag(scheduler, tenant_id, f"recover-{uuid4().hex[:8]}", max_attempts=3)
    ready = scheduler.ready_tasks(limit=1, workflow_run_id=run)
    assert ready
    agent = scheduler.claim_task(ready[0].id, "agent", "model", f"agent-{uuid4().hex}", lease_seconds=30, worker_id="ghost-worker")
    _expire_lease(tenant_id, ready[0].id)
    # No heartbeat row exists for ghost-worker => the sweeper may reclaim.
    result = scheduler.recover_stale_leases(worker_grace_seconds=90)
    assert result["recovered"] == 1
    assert _task_status(tenant_id, ready[0].id) == "ready"
    with _connect(tenant_id) as connection, connection.cursor() as cur:
        cur.execute("SELECT status, error_detail FROM control.agent_runs WHERE id=%s", (agent.id,))
        status, error = cur.fetchone()
        assert status == "cancelled"
        assert error == "lease_expired_worker_lost"


def test_recover_stale_leases_does_not_steal_from_live_worker() -> None:
    scheduler = Scheduler(DB)
    tenant_id = _tenant_id()
    _mission, run = _make_dag(scheduler, tenant_id, f"live-{uuid4().hex[:8]}")
    ready = scheduler.ready_tasks(limit=1, workflow_run_id=run)
    assert ready
    agent = scheduler.claim_task(ready[0].id, "agent", "model", f"agent-{uuid4().hex}", lease_seconds=30, worker_id="live-worker")
    write_worker_heartbeat(DB, worker_id="live-worker", tenant_slug="local-dev", loop_count=5)
    _expire_lease(tenant_id, ready[0].id)
    result = scheduler.recover_stale_leases(worker_grace_seconds=90)
    assert result["recovered"] == 0
    assert _task_status(tenant_id, ready[0].id) == "running"
    scheduler.fail_agent(agent.id, "live-worker-test-done")


def test_recover_stale_leases_fails_exhausted_task_and_cascades() -> None:
    scheduler = Scheduler(DB)
    tenant_id = _tenant_id()
    _mission, run = _make_dag(scheduler, tenant_id, f"exhaust-{uuid4().hex[:8]}", max_attempts=1)
    ready = scheduler.ready_tasks(limit=1, workflow_run_id=run)
    assert ready
    scheduler.claim_task(ready[0].id, "agent", "model", f"agent-{uuid4().hex}", lease_seconds=30, worker_id="ghost2")
    _expire_lease(tenant_id, ready[0].id)
    result = scheduler.recover_stale_leases(worker_grace_seconds=90)
    assert result["exhausted"] == 1
    assert _task_status(tenant_id, ready[0].id) == "failed"
    with _connect(tenant_id) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT status FROM control.task_runs WHERE workflow_run_id=%s",
            (run,),
        )
        assert sorted(row[0] for row in cur.fetchall()) == ["cancelled", "cancelled", "failed"]


def test_worker_heartbeat_upsert_and_liveness() -> None:
    write_worker_heartbeat(DB, worker_id="hb-test", tenant_slug="local-dev", loop_count=1, published=3)
    write_worker_heartbeat(DB, worker_id="hb-test", tenant_slug="local-dev", loop_count=7, published=4, tasks=1)
    workers = {w["worker_id"]: w for w in worker_liveness(DB, grace_seconds=90)}
    assert "hb-test" in workers
    assert workers["hb-test"]["loop_count"] == 7
    assert workers["hb-test"]["published"] == 4
    assert workers["hb-test"]["tasks"] == 1
    assert workers["hb-test"]["stale"] is False
    # Stale threshold: a heartbeat far in the past is reported stale.
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("UPDATE ops.worker_heartbeats SET last_seen=now()-interval '10 minutes' WHERE worker_id='hb-test'")
    workers = {w["worker_id"]: w for w in worker_liveness(DB, grace_seconds=90)}
    assert workers["hb-test"]["stale"] is True


def test_checks_return_structured_findings() -> None:
    data_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".data")
    findings = collect_findings(
        DB, data_dir=data_dir,
        expected_head=EXPECTED_MIGRATION_HEAD,
    )
    assert {f.check for f in findings} == {
        "disk_usage", "outbox_backlog", "stuck_tasks", "audit_growth", "worker_liveness", "migration_head",
    }
    for finding in findings:
        assert finding.severity in {"ok", "warn", "crit"}
    migration = next(f for f in findings if f.check == "migration_head")
    assert migration.severity == "ok"
    assert migration.message == EXPECTED_MIGRATION_HEAD
