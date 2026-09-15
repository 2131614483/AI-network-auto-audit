"""A failed node must never leave an unfinished attempt behind.

``PortBoundExecutor`` opens an attempt *before* it prepares the node.  Two
paths used to escape without closing it:

* ``_bind_inputs`` ran outside the node's ``try``, so when an upstream node
  produced no artifact the resulting ``RuntimeError`` propagated out of
  ``execute()`` — aborting every remaining node and leaving the attempt at
  ``status='running'`` / ``finished_at=NULL`` forever;
* the loop itself had no per-node guard for preparation failures (unsafe
  identifier, mkdir error).

A stuck attempt is not cosmetic: ``list_plan_runs`` derives a run's status from
attempt states, so the run reads as *running* indefinitely and the canvas
projection shows a live node that will never finish.

No real plugin subprocess runs here — the runtime is a stub whose producer
always fails, which is exactly the scenario that used to leak.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2

from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.dag_persistence import AttemptStore
from packages.plugin_topology.ports_executor import PortBoundExecutor
from packages.policy.engine import PolicyEngine

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"

_PRODUCER = "producer"
_CONSUMER = "consumer"


def _port(port_id: str, direction: str, schema_ref: str) -> dict:
    return {
        "port_id": port_id,
        "direction": direction,
        "schema_ref": schema_ref,
        "schema_version": "1.0.0",
        "schema_sha256": "a" * 64,
        "media_type": "application/json",
        "required": True,
        "cardinality": "one",
        "classification": "internal",
        "transport": "artifact_ref",
    }


class _ProducerFailsRuntime:
    """Stub runtime: the producer raises, so the consumer has no input."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, invocation: object, policy: object) -> object:
        capability = str(getattr(invocation, "capability", ""))
        self.calls.append(capability)
        if capability == "audit.ledger.validate":
            raise RuntimeError("producer exploded")
        raise AssertionError(f"consumer must not be invoked: {capability}")


def _tenant_id() -> UUID:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        return UUID(str(cur.fetchone()[0]))


def _seed(tmp_path: Path) -> ArtifactInput:
    path = tmp_path / "ledger.csv"
    raw = "凭证号,日期,科目,摘要,借方,贷方\nE1,2026-01-05,1001,测试,10.00,10.00\n".encode("utf-8-sig")
    path.write_bytes(raw)
    return ArtifactInput(
        artifact_id=uuid4(),
        tenant_id=uuid4(),
        uri=path.resolve().as_uri(),
        media_type="text/csv",
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        classification="audit_ledger",
    )


def _plan(plan_key: str):
    nodes = [
        {
            "node_instance_id": _PRODUCER,
            "plugin_id": "audit.ledger-quality",
            "capability": "audit.ledger.validate",
            "input_ports": [_port("ledger", "input", "ledger-artifact-ref")],
            "output_ports": [_port("candidates", "output", "audit-quality-candidates")],
        },
        {
            "node_instance_id": _CONSUMER,
            "plugin_id": "quant.experiment-evaluator",
            "capability": "quant.experiment.evaluate",
            "input_ports": [_port("candidates", "input", "audit-quality-candidates")],
            "output_ports": [_port("evaluation", "output", "experiment-evaluation")],
        },
    ]
    edges = [
        {
            "edge_id": "e1",
            "source_instance": _PRODUCER,
            "source_port": "candidates",
            "target_instance": _CONSUMER,
            "target_port": "candidates",
        }
    ]
    return compile_plan(
        nodes=nodes,
        edges=edges,
        plan_key=plan_key,
        seed_inputs={(_PRODUCER, "ledger")},
    )


def _attempts_for(run_id: str) -> list[dict]:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(_tenant_id()),))
        cur.fetchone()
        cur.execute(
            """SELECT node_instance_id, status, finished_at, error_kind, error_message
               FROM control.node_attempts WHERE tenant_id=%s AND run_id=%s
               ORDER BY created_at""",
            (str(_tenant_id()), run_id),
        )
        return [
            {
                "node_instance_id": row[0],
                "status": row[1],
                "finished_at": row[2],
                "error_kind": row[3],
                "error_message": row[4],
            }
            for row in cur.fetchall()
        ]


def test_downstream_attempt_is_finalized_when_the_producer_fails(tmp_path: Path) -> None:
    """The defect this guards: an uncaught bind error left the attempt open."""
    tenant_id = _tenant_id()
    run_id = str(uuid4())
    runtime = _ProducerFailsRuntime()
    executor = PortBoundExecutor(
        plan=_plan(f"plan-{uuid4().hex[:12]}"),
        runtime=runtime,  # type: ignore[arg-type]
        policy=PolicyEngine(allow=["audit.ledger.validate", "quant.experiment.evaluate"]),
        tenant_id=tenant_id,
        trace_id=str(uuid4()),
        staging_root=tmp_path,
        idempotency_key=f"finalize-{run_id}",
        seed_inputs={(_PRODUCER, "ledger"): _seed(tmp_path)},
        persistence=AttemptStore(TEST_DB),
        run_id=run_id,
    )

    # Before the fix this raised out of execute() instead of returning.
    outcome = executor.execute()

    assert outcome["status"] == "failed"
    assert [entry["status"] for entry in outcome["entries"]] == ["failed", "failed"]
    # the consumer failed for the honest reason, not because the loop aborted
    consumer_entry = outcome["entries"][1]
    assert "producer artifact not ready" in consumer_entry["error"]
    assert runtime.calls == ["audit.ledger.validate"]

    attempts = _attempts_for(run_id)
    assert len(attempts) == 2
    # the core invariant: nothing left running / unfinished
    assert all(attempt["status"] != "running" for attempt in attempts)
    assert all(attempt["finished_at"] is not None for attempt in attempts)
    assert {attempt["node_instance_id"] for attempt in attempts} == {_PRODUCER, _CONSUMER}


def test_run_projection_does_not_report_a_stuck_run(tmp_path: Path) -> None:
    """``list_plan_runs`` must not derive 'running' from a leaked attempt."""
    from packages.plugin_topology.service import TopologyService

    tenant_id = _tenant_id()
    run_id = str(uuid4())
    executor = PortBoundExecutor(
        plan=_plan(f"plan-{uuid4().hex[:12]}"),
        runtime=_ProducerFailsRuntime(),  # type: ignore[arg-type]
        policy=PolicyEngine(allow=["audit.ledger.validate", "quant.experiment.evaluate"]),
        tenant_id=tenant_id,
        trace_id=str(uuid4()),
        staging_root=tmp_path,
        idempotency_key=f"projection-{run_id}",
        seed_inputs={(_PRODUCER, "ledger"): _seed(tmp_path)},
        persistence=AttemptStore(TEST_DB),
        run_id=run_id,
    )
    executor.execute()

    runs = {str(item["run_id"]): item for item in TopologyService(TEST_DB).list_plan_runs(tenant_id, limit=200)}
    assert run_id in runs, "the run must be visible in the feed"
    assert runs[run_id]["status"] == "failed"
    assert runs[run_id]["attempt_total"] == 2
    assert runs[run_id]["attempt_failed"] == 2


def test_unsafe_plan_key_fails_the_node_without_leaving_it_running(tmp_path: Path) -> None:
    """A plan that bypassed the compiler must still close its attempt."""
    from packages.plugin_topology.ir import ExecutionPlan

    tenant_id = _tenant_id()
    run_id = str(uuid4())
    plan = _plan(f"plan-{uuid4().hex[:12]}")
    # Simulate a plan loaded straight from storage, never recompiled.
    object.__setattr__(plan, "plan_key", "plan-../../../../pwned")
    executor = PortBoundExecutor(
        plan=plan,
        runtime=_ProducerFailsRuntime(),  # type: ignore[arg-type]
        policy=PolicyEngine(allow=["audit.ledger.validate", "quant.experiment.evaluate"]),
        tenant_id=tenant_id,
        trace_id=str(uuid4()),
        staging_root=tmp_path,
        idempotency_key=f"unsafe-{run_id}",
        seed_inputs={(_PRODUCER, "ledger"): _seed(tmp_path)},
        persistence=AttemptStore(TEST_DB),
        run_id=run_id,
    )

    outcome = executor.execute()
    assert outcome["status"] == "failed"
    assert all(entry["status"] == "failed" for entry in outcome["entries"])
    assert any("plan_key" in (entry.get("error") or "") for entry in outcome["entries"])

    attempts = _attempts_for(run_id)
    assert all(attempt["finished_at"] is not None for attempt in attempts)
    # nothing escaped the staging root
    outside = list(tmp_path.parent.glob("pwned*"))
    assert outside == []
    assert ExecutionPlan is not None  # imported for the object.__setattr__ trick
