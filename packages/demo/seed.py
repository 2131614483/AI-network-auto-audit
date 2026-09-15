"""Idempotent demo seed for the local control plane.

Every feature-facing domain (control missions, AIOps, quant, audit) is
provisioned through its real service code path, never by ad-hoc INSERT
scripts, so the demo projection is reproducible on an empty database and the
mission/workflow/task/agent state chain is always internally consistent
(planned -> running -> completed/failed, with cancelled cascades on failure).

The seed is safe to re-run: control uses the scheduler's idempotency keys,
AIOps alerts upsert by fingerprint and incidents are guarded by title, quant
and audit guards skip when their demo rows already exist, and the terminal
state backfill only advances runs whose tasks are already terminal.
"""

from __future__ import annotations

import csv
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from packages.aiops.engine import AIOpsEngine
from packages.audit.pipeline import AuditPipeline
from packages.control.executor import DEFAULT_DEMO_HANDLERS, run_mission_dag
from packages.control.scheduler import PluginExecutor, Scheduler
from packages.policy.engine import PolicyEngine
from packages.quant.backtest import run_csv_backtest

DEMO_WORKFLOW_KEY = "demo-dag"
DEMO_WORKFLOW_VERSION = "1.0.0"


@dataclass(slots=True)
class SeedReport:
    missions: int = 0
    workflow_runs: int = 0
    task_runs: int = 0
    agent_runs: int = 0
    incidents: int = 0
    alerts: int = 0
    backtests: int = 0
    engagements: int = 0
    reconciled_runs: int = 0
    reconciled_missions: int = 0
    notes: list[str] = field(default_factory=list)


def _ensure_demo_policy(database_url: str) -> None:
    """One idempotent allow set for demo execution capabilities."""
    import psycopg2
    from psycopg2.extras import Json

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            row = cur.fetchone()
            if row is None:
                raise ValueError("tenant not found: local-dev")
            tenant_id = row[0]
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.execute(
                """INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules)
                VALUES(%s,'demo-execution-allow',1,'active',%s)
                ON CONFLICT(tenant_id,name,version) DO NOTHING""",
                (
                    tenant_id,
                    Json([
                        {
                            "rule_id": str(uuid4()),
                            "effect": "allow",
                            "match": {
                                "capabilities": ["scheduler.a", "scheduler.b", "scheduler.c", "aiops.playbook.*"],
                            },
                        }
                    ]),
                ),
            )


def _mission_exists(database_url: str, title: str) -> bool:
    import psycopg2

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_row = cur.fetchone()
        assert tenant_row is not None
        tenant_id = tenant_row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "SELECT 1 FROM control.missions WHERE tenant_id=%s AND title=%s LIMIT 1",
            (tenant_id, title),
        )
        return cur.fetchone() is not None


def _seed_control(database_url: str, report: SeedReport) -> SeedReport:
    scheduler = Scheduler(database_url)
    executor = PluginExecutor(scheduler)
    for capability, handler in DEFAULT_DEMO_HANDLERS.items():
        executor.register(capability, handler)
    scheduler.create_workflow(
        DEMO_WORKFLOW_KEY, "Demo DAG", DEMO_WORKFLOW_VERSION,
        {
            "nodes": [
                {"key": "a", "capability": "scheduler.a"},
                {"key": "b", "capability": "scheduler.b", "depends_on": ["a"]},
                {"key": "c", "capability": "scheduler.c", "depends_on": ["b"]},
            ]
        },
    )

    # 1) Completed mission: the whole DAG genuinely runs through the executor.
    if not _mission_exists(database_url, "Demo: full success run"):
        mission_success = scheduler.create_mission(
            "Demo: full success run", "seed a completed mission", "audit",
            idempotency_key="demo-mission-success",
        )
        run_id = scheduler.start_run(
            mission_success, DEMO_WORKFLOW_KEY, DEMO_WORKFLOW_VERSION,
            {"input": 42}, "demo-run-success",
        )
        result = run_mission_dag(scheduler, executor, run_id, stop_at_terminal=True)
        report.missions += 1
        report.workflow_runs += 1
        report.task_runs += result.task_count
        report.agent_runs += result.agent_count

    # 2) Failed mission: first node fails, dependents cancel, run goes terminal.
    if not _mission_exists(database_url, "Demo: failure and cascade"):
        mission_failure = scheduler.create_mission(
            "Demo: failure and cascade", "seed a failed mission with rollback evidence", "aiops",
            idempotency_key="demo-mission-failure",
        )
        run_failure = scheduler.start_run(
            mission_failure, DEMO_WORKFLOW_KEY, DEMO_WORKFLOW_VERSION,
            {"input": -1}, "demo-run-failure",
        )
        first = scheduler.ready_tasks(limit=1, workflow_run_id=run_failure)
        if first:
            claim = scheduler.claim_task(
                first[0].id, "scheduler-worker", "local-demo",
                f"agent-{uuid4().hex}", lease_seconds=300, worker_id="seed-demo",
            )
            scheduler.fail_agent(claim.id, "simulated outage: primary node unavailable")
        report.missions += 1
        report.workflow_runs += 1

    # 3) Running mission: first task claimed and left live with a fresh lease.
    if not _mission_exists(database_url, "Demo: live running mission"):
        mission_running = scheduler.create_mission(
            "Demo: live running mission", "seed a mission that is actively running", "control",
            idempotency_key="demo-mission-running",
        )
        run_running = scheduler.start_run(
            mission_running, DEMO_WORKFLOW_KEY, DEMO_WORKFLOW_VERSION,
            {"input": 7}, "demo-run-running",
        )
        ready = scheduler.ready_tasks(limit=1, workflow_run_id=run_running)
        if ready:
            scheduler.claim_task(
                ready[0].id, "scheduler-worker", "local-demo",
                f"agent-{uuid4().hex}", lease_seconds=3600, worker_id="seed-demo",
            )
        report.missions += 1
        report.workflow_runs += 1
        report.notes.append("running mission is left live; a running worker will pick it up after lease expiry")
    return report


def _seed_aiops(database_url: str, report: SeedReport) -> SeedReport:
    engine = AIOpsEngine(database_url, PolicyEngine(allow=["aiops.playbook.*"]))

    def incident_exists(title: str) -> bool:
        import psycopg2

        with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            row = cur.fetchone()
            assert row is not None
            tenant_id = row[0]
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.execute(
                "SELECT 1 FROM aiops.incidents WHERE tenant_id=%s AND title=%s",
                (tenant_id, title),
            )
            return cur.fetchone() is not None

    if not incident_exists("demo-monitor: critical-disk-fill"):
        incident = engine.ingest_alert(
            "demo-monitor", "critical-disk-fill", "critical",
            {"disk": "/data", "usage_pct": 97},
        )
        proposal = engine.propose(incident, "restart-worker", {"kind": "restart", "target": "worker"}, risk_class="high")
        change = engine.request_change(proposal)
        engine.approve_change(change)
        execution, _status = engine.execute(proposal, mode="canary", change_request_id=change)
        engine.verify(execution, healthy=True)
        report.incidents += 1
        report.alerts += 1

    if not incident_exists("demo-monitor: degraded-replica"):
        incident = engine.ingest_alert(
            "demo-monitor", "degraded-replica", "medium",
            {"replica": "r3", "lag_seconds": 180},
        )
        proposal = engine.propose(incident, "restart-worker", {"kind": "restart", "target": "replica"}, risk_class="medium")
        change = engine.request_change(proposal)
        engine.approve_change(change)
        execution, _status = engine.execute(proposal, mode="canary", change_request_id=change)
        engine.verify(execution, healthy=False)  # rollback sample: circuit opens
        report.incidents += 1
        report.alerts += 1

    for fingerprint, severity in (
        ("low-cpu-blip", "low"),
        ("low-memory-blip", "low"),
        ("low-latency-blip", "low"),
    ):
        engine.ingest_alert("demo-monitor", fingerprint, severity, {"value": 1})
        report.alerts += 1
    return report


def _seed_quant(database_url: str, report: SeedReport) -> SeedReport:
    import psycopg2

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "SELECT 1 FROM quant.backtests WHERE tenant_id=%s AND strategy_key='demo_momentum' LIMIT 1",
            (tenant_id,),
        )
        if cur.fetchone() is not None:
            return report
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as handle:
        handle.write("date,close\n")
        price = 100.0
        for day in range(1, 61):
            price = round(price * (1.0 + (0.01 if day % 3 else -0.005)), 4)
            handle.write(f"2026-01-{day:02d},{price}\n")
        csv_path = Path(handle.name)
    try:
        run_csv_backtest(database_url, csv_path, strategy_key="demo_momentum")
        report.backtests += 1
    finally:
        csv_path.unlink(missing_ok=True)
    return report


def _seed_audit(database_url: str, report: SeedReport) -> SeedReport:
    import psycopg2

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "SELECT 1 FROM audit.engagements WHERE tenant_id=%s AND name='Demo Ledger Review' LIMIT 1",
            (tenant_id,),
        )
        if cur.fetchone() is not None:
            return report
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "description", "amount"])
        writer.writerow(["2026-08-01", "rent", "120000"])
        writer.writerow(["2026-08-02", "vendor payment", "2500000"])  # large amount
        writer.writerow(["2026-08-02", "vendor payment", "2500000"])  # duplicate row
        writer.writerow(["2026-08-03", "misc", ""])  # missing amount
        writer.writerow(["2026-08-04", "salary", "90000"])
        csv_path = Path(handle.name)
    try:
        pipeline = AuditPipeline(database_url)
        result = pipeline.run_ledger_csv(csv_path, engagement_name="Demo Ledger Review")
        report.engagements += 1
        report.notes.append(f"ledger anomalies: {result.anomalies}")
    finally:
        csv_path.unlink(missing_ok=True)
    return report


def seed_local_demo(database_url: str, *, reconcile: bool = True) -> SeedReport:
    """Provision every demo domain idempotently and reconcile terminal states."""
    report = SeedReport()
    _ensure_demo_policy(database_url)
    report = _seed_control(database_url, report)
    report = _seed_aiops(database_url, report)
    report = _seed_quant(database_url, report)
    report = _seed_audit(database_url, report)
    if reconcile:
        scheduler = Scheduler(database_url)
        counts = scheduler.reconcile_workflow_states()
        report.reconciled_runs = counts["runs_advanced"]
        report.reconciled_missions = counts["missions_advanced"]
    return report
