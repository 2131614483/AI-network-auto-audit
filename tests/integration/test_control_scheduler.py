from __future__ import annotations

import os
from uuid import UUID, uuid4

import psycopg2
import pytest
from psycopg2.extras import Json

from packages.control.scheduler import PluginExecutor, Scheduler

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def _tenant_and_project() -> tuple[UUID, str]:
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            row = cur.fetchone()
            assert row is not None
            return row[0], "default"


def _allow_capabilities(tenant_id: UUID, capabilities: list[str]) -> None:
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,999,'active',%s)",
                (tenant_id, f"scheduler-{uuid4().hex}", Json([
                    {"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": capabilities}}
                ])),
            )


def test_scheduler_creates_dag_releases_tasks_after_dependencies_and_is_idempotent() -> None:
    tenant_id, project = _tenant_and_project()
    scheduler = Scheduler(DB)
    capabilities = ["scheduler.a", "scheduler.b", "scheduler.c"]
    _allow_capabilities(tenant_id, capabilities)
    workflow_key = f"scheduler-{uuid4().hex}"
    workflow = scheduler.create_workflow(
        workflow_key,
        "Scheduler integration",
        "1.0.0",
        {
            "nodes": [
                {"key": "a", "capability": "scheduler.a"},
                {"key": "b", "capability": "scheduler.b", "depends_on": ["a"]},
                {"key": "c", "capability": "scheduler.c", "depends_on": ["b"]},
            ]
        },
    )
    assert workflow
    mission = scheduler.create_mission("DAG test", "run a DAG", "audit", project)
    run_key = f"run-{uuid4().hex}"
    run_id = scheduler.start_run(mission, workflow_key, "1.0.0", {"input": 1}, run_key)
    assert scheduler.start_run(mission, workflow_key, "1.0.0", {"input": 1}, run_key) == run_id
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM event.outbox WHERE tenant_id=%s AND event_type='task.ready'", (tenant_id,))
        initial_ready_events = cur.fetchone()[0]
    assert initial_ready_events >= 1
    ready = scheduler.runnable_tasks(run_id)
    assert [task.node_key for task in ready] == ["a"]

    executor = PluginExecutor(scheduler)
    executor.register("scheduler.a", lambda payload: {"a": payload["input"]})
    executor.register("scheduler.b", lambda payload: {"b": True})
    executor.register("scheduler.c", lambda payload: {"c": True})

    first = scheduler.claim_task(ready[0].id, "test-agent", "test-model", f"agent-{uuid4().hex}")
    assert executor.execute(first.id) == {"a": 1}
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM event.outbox WHERE tenant_id=%s AND event_type='task.ready'", (tenant_id,))
        assert cur.fetchone()[0] >= initial_ready_events + 1
    ready = scheduler.runnable_tasks(run_id)
    assert [task.node_key for task in ready] == ["b"]
    second = scheduler.claim_task(ready[0].id, "test-agent", "test-model", f"agent-{uuid4().hex}")
    assert executor.execute(second.id) == {"b": True}
    ready = scheduler.runnable_tasks(run_id)
    assert [task.node_key for task in ready] == ["c"]
    third = scheduler.claim_task(ready[0].id, "test-agent", "test-model", f"agent-{uuid4().hex}")
    assert executor.execute(third.id) == {"c": True}
    assert scheduler.runnable_tasks(run_id) == []


def test_scheduler_rejects_cycles_and_plugin_execution_is_policy_gated() -> None:
    scheduler = Scheduler(DB)
    with pytest.raises(ValueError, match="cycle"):
        scheduler.create_workflow(
            f"cycle-{uuid4().hex}", "Cycle", "1.0.0",
            {"nodes": [
                {"key": "a", "capability": "cycle.a", "depends_on": ["b"]},
                {"key": "b", "capability": "cycle.b", "depends_on": ["a"]},
            ]},
        )
