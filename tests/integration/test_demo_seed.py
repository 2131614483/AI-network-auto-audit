"""R0 contracts: the demo seed provisions every domain and is idempotent.

The seed is the single authoritative demo-data entry point: after it runs,
every UI-facing domain has rows, and the mission/workflow/task/agent state
chain is internally consistent (the legacy "planned mission with completed
tasks" projection is impossible to reach through the real scheduler).
"""

from __future__ import annotations

import os
from uuid import UUID

import psycopg2

from packages.demo.seed import seed_local_demo

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def _counts() -> dict[str, int]:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = UUID(str(cur.fetchone()[0]))
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute("SELECT count(*) FROM control.missions")
        missions = int(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM aiops.incidents")
        incidents = int(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM aiops.alerts")
        alerts = int(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM quant.backtests WHERE strategy_key='demo_momentum'")
        backtests = int(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM audit.engagements WHERE name='Demo Ledger Review'")
        engagements = int(cur.fetchone()[0])
    return {
        "missions": missions, "incidents": incidents, "alerts": alerts,
        "backtests": backtests, "engagements": engagements,
    }


def _assert_state_consistency() -> None:
    """Every mission/workflow/task chain must be reachable by the scheduler."""
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = UUID(str(cur.fetchone()[0]))
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute(
            """SELECT m.id,m.status,w.status,t.status
            FROM control.missions m
            LEFT JOIN control.workflow_runs w ON w.mission_id=m.id AND w.tenant_id=m.tenant_id
            LEFT JOIN control.task_runs t ON t.workflow_run_id=w.id AND t.tenant_id=w.tenant_id""",
        )
        rows = cur.fetchall()
    assert rows, "seed produced no control data"
    terminal = {"completed", "failed", "cancelled"}
    by_mission: dict[str, dict[str, set[str]]] = {}
    for mission_id, mission_status, workflow_status, task_status in rows:
        entry = by_mission.setdefault(str(mission_id), {"mission": mission_status, "workflows": set(), "tasks": set()})
        if workflow_status is not None:
            entry["workflows"].add(workflow_status)
            if task_status is not None:
                entry["tasks"].add(task_status)
    for mission_id, entry in by_mission.items():
        mission, workflows, tasks = entry["mission"], entry["workflows"], entry["tasks"]
        if mission == "planned":
            assert workflows <= {"pending"}, f"planned mission has non-pending workflow: {mission_id}"
        elif mission == "completed":
            assert workflows <= {"completed"}, f"completed mission has non-completed workflow: {mission_id}"
            assert tasks <= {"completed"}, f"completed mission has unfinished task: {mission_id}"
        elif mission == "failed":
            assert workflows <= terminal, f"failed mission has unfinished workflow: {mission_id}"
            assert "failed" in workflows or "cancelled" in workflows, f"failed mission has no failed workflow: {mission_id}"
            assert tasks <= terminal, f"failed mission has unfinished task: {mission_id}"
        elif mission == "running":
            assert workflows - terminal, f"running mission has no active workflow: {mission_id}"
        else:
            raise AssertionError(f"unexpected mission status: {mission}")


def test_seed_provisions_every_domain_and_is_idempotent() -> None:
    first = seed_local_demo(DB)
    assert first.missions <= 3  # creates at most the three demo missions
    before = _counts()
    assert before["missions"] >= 3
    assert before["incidents"] >= 2
    assert before["alerts"] >= 3
    assert before["backtests"] >= 1
    assert before["engagements"] >= 1

    seed_local_demo(DB)  # second run
    after = _counts()
    # Idempotent: control missions are title-guarded, quant/audit are guarded,
    # AIOps alerts upsert by fingerprint.
    assert after["missions"] == before["missions"]
    assert after["incidents"] == before["incidents"]
    assert after["backtests"] == before["backtests"]
    assert after["engagements"] == before["engagements"]


def test_seed_state_chain_is_internally_consistent() -> None:
    seed_local_demo(DB)
    _assert_state_consistency()
