"""CW3 contract tests: unified DAG execution loop.

Acceptance (方案 第13节 CW3 退出条件 :435):
  - first ledger chain with real multi-input passes, and every data edge and
    every attempt is traceable;
  - worker kill / late write-back / duplicate submit are controllable:
    stale leases are recovered, late fencing writes are rejected with a
    traced event, repeat submits are idempotent;
  - retries create a new attempt while the logical node identity stays stable.

The plan used is the CW1 port-bound shape (two seeded ledger producers +
fan-out consumers through a real registered adapter), now executed through
the service with per-node short-transaction persistence.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import url2pathname
from uuid import UUID, uuid4

import psycopg2
import pytest

from packages.experience import ExperienceProjector
from packages.plugin_runtime.layout import runtime_dir
from packages.plugin_runtime.runner import ArtifactInput, load_verified_binding
from packages.plugin_topology import port_adapters  # noqa: F401  (registers the real port adapter)
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.dag_persistence import (
    AttemptStore,
    FencingError,
    recover_and_retry_once,
)
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"
_SHA64 = "a" * 64
_ADAPTER = "candidates-to-backtest"
_ALLOW = [
    "topology.chain.execute", "topology.chain.execute.isolated",
    "audit.ledger.validate", "quant.experiment.evaluate",
]


def _port(port_id: str, direction: str, *, schema_ref: str = "artifact-ref",
          required: bool = True, cardinality: str = "one") -> dict:
    return {
        "port_id": port_id, "direction": direction, "schema_ref": schema_ref,
        "schema_version": "1.0.0", "schema_sha256": _SHA64,
        "media_type": "application/json", "required": required,
        "cardinality": cardinality, "classification": "internal",
        "transport": "artifact_ref",
    }


def _node(node_instance_id: str, capability: str, plugin_id: str, *,
          inputs: tuple[dict, ...] = (), outputs: tuple[dict, ...] = ()) -> dict:
    return {
        "node_instance_id": node_instance_id, "plugin_id": plugin_id,
        "capability": capability, "input_ports": list(inputs),
        "output_ports": list(outputs),
    }


def _edge(edge_id: str, source_instance: str, source_port: str,
          target_instance: str, target_port: str) -> dict:
    return {
        "edge_id": edge_id, "source_instance": source_instance,
        "source_port": source_port, "target_instance": target_instance,
        "target_port": target_port, "adapter": _ADAPTER,
    }


_LEDGER_IN = _port("ledger", "input", schema_ref="ledger-artifact-ref")
_LEDGER_OUT = _port("candidates", "output", schema_ref="audit-quality-candidates")
_EXPERIMENT_IN = _port("experiment", "input", schema_ref="backtest-report")
_EVAL_OUT = _port("evaluation", "output", schema_ref="experiment-evaluation")
_BUDGET = {"max_chain_length": 8, "max_candidates": 8, "max_latency_ms": 5000}


def _write_seed(staging: Path, name: str, rows: str) -> ArtifactInput:
    path = staging / "inputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rows, encoding="utf-8-sig")
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="text/csv", sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_ledger",
    )


def _ledger_csv(tag: str) -> str:
    return (
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        f"E1,2026-01-05,1101,{tag}-收款,100.00,100.00\n"
        f"E2,2026-01-06,1102,{tag}-划转,200.00,200.00\n"
    )


def _build_plan() -> object:
    nodes = [
        _node("ledger-a", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,)),
        _node("ledger-b", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,)),
        _node("consumer-x", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,), outputs=(_EVAL_OUT,)),
        _node("consumer-y", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,), outputs=(_EVAL_OUT,)),
    ]
    edges = [
        _edge("e1", "ledger-a", "candidates", "consumer-x", "experiment"),
        _edge("e2", "ledger-a", "candidates", "consumer-y", "experiment"),
    ]
    return compile_plan(
        nodes=nodes, edges=edges, budget=_BUDGET, plan_key=f"cw3-{uuid4().hex[:8]}",
        seed_inputs={("ledger-a", "ledger"), ("ledger-b", "ledger")},
    )


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


@pytest.fixture()
def tenant_id() -> UUID:
    with psycopg2.connect(TEST_DB) as connection:
        return _tenant(connection)


# -- 1. first ledger chain: real multi-input, persisted, fully traceable --------


def test_first_ledger_chain_real_multi_input_persisted(tmp_path: Path, tenant_id: UUID) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    seed_a = _write_seed(staging, "ledger-a.csv", _ledger_csv("A"))
    seed_b = _write_seed(staging, "ledger-b.csv", _ledger_csv("B"))
    plan = _build_plan()
    service = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW))
    result = service.start_plan_run(
        plan,
        seed_inputs={("ledger-a", "ledger"): seed_a, ("ledger-b", "ledger"): seed_b},
        worker_id="cw3-test-worker",
        staging_root=staging,
    )
    assert result["status"] == "succeeded"
    attempts = result["attempts"]
    assert {a["node_instance_id"] for a in attempts} == {"ledger-a", "ledger-b", "consumer-x", "consumer-y"}
    assert all(a["status"] == "succeeded" for a in attempts)
    by_node = {a["node_instance_id"]: a for a in attempts}
    # fan-out consumers both bound ledger-a's candidates port (same sha)
    x = by_node["consumer-x"]["input_bindings"]["experiment"]
    y = by_node["consumer-y"]["input_bindings"]["experiment"]
    assert x["source_instance"] == "ledger-a" and x["source_port"] == "candidates"
    assert x["sha256"] == y["sha256"]
    # ledger-a and ledger-b are independent producers with distinct seeds
    a_in = by_node["ledger-a"]["input_bindings"]["ledger"]["sha256"]
    b_in = by_node["ledger-b"]["input_bindings"]["ledger"]["sha256"]
    assert a_in == seed_a.sha256 and b_in == seed_b.sha256 and a_in != b_in
    # every output ref exists on disk with matching sha (data-layer drill-down)
    for node_id, attempt in by_node.items():
        for ref in attempt["output_refs"].values():
            uri = ref["uri"]
            p = Path(url2pathname(uri[len("file://"):] if uri.startswith("file://") else uri))
            assert p.is_file(), (node_id, ref)
            assert hashlib.sha256(p.read_bytes()).hexdigest() == ref["sha256"]

    # -- which implementation ran (parity with the M6 isolated ledger) ----------
    # The attempt row must pin the plugin version and the runtime bytes, so
    # "which version of which plugin produced this artifact" is answerable from
    # the database alone — not only from the archived plan.
    for node_id, attempt in by_node.items():
        binding = load_verified_binding(attempt["plugin_id"])
        assert attempt["plugin_version"] == binding.version, node_id
        assert attempt["runtime_code_sha256"] == hashlib.sha256(
            (runtime_dir(attempt["plugin_id"]) / "runtime.py").read_bytes()
        ).hexdigest(), node_id

    edges = result["edges"]
    keys = {e["key"] for e in edges}
    # plan edges: fan-out from ledger-a's candidates port
    assert {
        ("ledger-a", "candidates", "consumer-x", "experiment"),
        ("ledger-a", "candidates", "consumer-y", "experiment"),
    } <= keys
    # seed injections are data-flow edges too (external authorized inputs)
    assert ("seed", "ledger", "ledger-a", "ledger") in keys
    assert ("seed", "ledger", "ledger-b", "ledger") in keys
    assert all(e["source_instance"] == "ledger-a" for e in edges
               if e["target_instance"].startswith("consumer"))


# -- 1b. a node that never reached the runtime records no implementation --------


def test_attempt_records_no_implementation_when_node_never_ran(
    tmp_path: Path, tenant_id: UUID
) -> None:
    """The version/hash columns are evidence, so absence must be honest.

    A node that fails before the runtime is reached (here: an unresolvable
    plugin) has no implementation hash to report.  It must stay ``''`` rather
    than borrow the version of some other plugin or a fabricated value — the
    same fail-closed stance the M6 ledger takes.
    """
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    seed = _write_seed(staging, "ledger-ghost.csv", _ledger_csv("G"))
    plan = compile_plan(
        nodes=[_node("ghost", "audit.not-a-real-capability", "audit.not-a-real-plugin",
                     inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,))],
        edges=[], budget=_BUDGET, plan_key=f"cw3-ghost-{uuid4().hex[:8]}",
        seed_inputs={("ghost", "ledger")},
    )
    service = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW))
    result = service.start_plan_run(
        plan,
        seed_inputs={("ghost", "ledger"): seed},
        worker_id="cw3-test-worker",
        staging_root=staging,
    )
    assert result["status"] == "failed"
    attempt = next(a for a in result["attempts"] if a["node_instance_id"] == "ghost")
    assert attempt["status"] == "failed"
    assert attempt["plugin_version"] == ""
    assert attempt["runtime_code_sha256"] == ""


# -- 2. fencing: stale worker late write-back is rejected and traced ------------


def test_stale_worker_fencing_rejected_and_traced(tenant_id: UUID) -> None:
    store = AttemptStore(TEST_DB)
    run_id = str(uuid4())
    first = store.begin_attempt(
        tenant_id=tenant_id, run_id=run_id, plan_key="cw3-fence",
        execution_hash="h" * 64, node_instance_id="n1",
        capability="cap.a", plugin_id="plugin-a", attempt_seq=1,
        trace_id=str(uuid4()), lease_seconds=-10,  # lease already expired
    )
    # repeat submit of the same logical node is idempotent (same attempt row)
    second = store.begin_attempt(
        tenant_id=tenant_id, run_id=run_id, plan_key="cw3-fence",
        execution_hash="h" * 64, node_instance_id="n1",
        capability="cap.a", plugin_id="plugin-a", attempt_seq=1,
        trace_id=str(uuid4()),
    )
    assert second["idempotent"] is True
    assert second["attempt_id"] == first["attempt_id"]

    # a fresh worker reclaims the expired-running attempt: new fencing token
    store.reclaim_attempt(tenant_id=tenant_id, attempt_id=first["attempt_id"])
    # the stale worker's late write-back with its old token is rejected
    with pytest.raises(FencingError):
        store.finish_attempt(
            tenant_id=tenant_id, attempt_id=first["attempt_id"],
            expected_lease_token=first["lease_token"], status="succeeded",
        )
    # rejection left a trace in the outbox
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "SELECT event_type FROM event.outbox "
            "WHERE tenant_id=%s AND event_type='node.finish.rejected' ORDER BY id DESC LIMIT 1",
            (str(tenant_id),),
        )
        row = cur.fetchone()
        assert row is not None and row[0] == "node.finish.rejected"


# -- 3. repeat submit: same logical operation has one effective result ----------


def test_repeat_submit_idempotent_same_attempt(tenant_id: UUID) -> None:
    store = AttemptStore(TEST_DB)
    run_id = str(uuid4())
    trace = str(uuid4())
    a = store.begin_attempt(
        tenant_id=tenant_id, run_id=run_id, plan_key="cw3-dup",
        execution_hash="d" * 64, node_instance_id="n1",
        capability="cap.a", plugin_id="plugin-a", attempt_seq=1, trace_id=trace,
    )
    b = store.begin_attempt(
        tenant_id=tenant_id, run_id=run_id, plan_key="cw3-dup",
        execution_hash="d" * 64, node_instance_id="n1",
        capability="cap.a", plugin_id="plugin-a", attempt_seq=1, trace_id=trace,
    )
    assert b["attempt_id"] == a["attempt_id"]
    assert b["idempotent"] is True
    assert len(store.query_attempts(tenant_id=tenant_id, run_id=run_id)) == 1


# -- 4. worker kill: stale lease recovered, retry creates a new attempt ---------


def test_worker_kill_recovery_and_retry_new_attempt(tenant_id: UUID) -> None:
    store = AttemptStore(TEST_DB)
    run_id = str(uuid4())
    dead = store.begin_attempt(
        tenant_id=tenant_id, run_id=run_id, plan_key="cw3-kill",
        execution_hash="k" * 64, node_instance_id="n1",
        capability="cap.a", plugin_id="plugin-a", attempt_seq=1,
        trace_id=str(uuid4()), lease_seconds=-10,  # already expired
    )
    assert dead["status"] == "running"
    recovered = recover_and_retry_once(
        TEST_DB, tenant_id=tenant_id, worker_id="cw3-sweeper",
        stale_before_seconds=0,
    )
    assert recovered["recovered"] >= 1
    after = store.query_attempts(tenant_id=tenant_id, run_id=run_id)
    assert after[0]["status"] == "failed"
    assert after[0]["error_kind"] == "lease_expired"

    retried = store.retry_attempt(
        tenant_id=tenant_id, run_id=run_id, node_instance_id="n1",
        trace_id=str(uuid4()),
    )
    assert retried["attempt_seq"] == 2
    all_attempts = store.query_attempts(tenant_id=tenant_id, run_id=run_id)
    assert {a["attempt_seq"] for a in all_attempts} == {1, 2}
    # the logical node identity stays stable across retries
    assert {a["node_instance_id"] for a in all_attempts} == {"n1"}


# -- 5. every edge and every attempt is traceable (recovery path too) -----------


def test_every_edge_and_attempt_traceable_after_recovery(tenant_id: UUID) -> None:
    store = AttemptStore(TEST_DB)
    run_id = str(uuid4())
    store.begin_attempt(
        tenant_id=tenant_id, run_id=run_id, plan_key="cw3-trace",
        execution_hash="t" * 64, node_instance_id="producer",
        capability="cap.a", plugin_id="plugin-a", attempt_seq=1,
        trace_id=str(uuid4()), lease_seconds=-10,
    )
    recover_and_retry_once(TEST_DB, tenant_id=tenant_id, worker_id="cw3-sweeper",
                           stale_before_seconds=0)
    store.retry_attempt(tenant_id=tenant_id, run_id=run_id,
                        node_instance_id="producer", trace_id=str(uuid4()))
    rows = store.query_attempts(tenant_id=tenant_id, run_id=run_id)
    assert len(rows) == 2
    assert all(r["run_id"] == run_id for r in rows)
    assert all(r["trace_id"] for r in rows)


# -- 6. runtime budget: oversize output is failed, not silently accepted --------


def test_runtime_budget_exceeded_fails_attempt(tmp_path: Path, tenant_id: UUID) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    seed = _write_seed(staging, "ledger.csv", _ledger_csv("A"))
    nodes = [
        _node("ledger-a", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,)),
        _node("consumer-x", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,), outputs=(_EVAL_OUT,)),
    ]
    edges = [_edge("e1", "ledger-a", "candidates", "consumer-x", "experiment")]
    plan = compile_plan(
        nodes=nodes, edges=edges,
        budget={"max_chain_length": 8, "max_candidates": 8, "max_latency_ms": 5000},
        plan_key=f"cw3-budget-{uuid4().hex[:8]}",
        seed_inputs={("ledger-a", "ledger")},
    )
    service = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW))
    result = service.start_plan_run(
        plan,
        seed_inputs={("ledger-a", "ledger"): seed},
        worker_id="cw3-test-worker",
        staging_root=staging,
        max_output_bytes=1,  # deliberately tiny: any artifact exceeds it
    )
    assert result["status"] == "failed"
    consumer = next(a for a in result["attempts"]
                    if a["node_instance_id"] == "consumer-x")
    assert consumer["status"] == "failed"
    assert consumer["error_kind"] == "budget_exceeded"


# -- 1c. the DAG surface feeds the experience layer ----------------------------

MIGRATOR_DB = "postgresql://audit_migrator:admin@localhost:5432/audit_network_test"


def _purge_experience(run_id: UUID, tenant_id: UUID) -> None:
    """Observations are append-only for the app role, so the migrator removes them.

    Rollups are then rebuilt from what remains, so the shared test database
    converges to the truth instead of keeping this run's counts.
    """
    with psycopg2.connect(MIGRATOR_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute("DELETE FROM experience.edge_observations WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM experience.node_observations WHERE run_id=%s", (str(run_id),))
        connection.commit()
    ExperienceProjector(TEST_DB).rebuild(tenant_id=tenant_id)


def test_start_plan_run_feeds_the_experience_layer(tmp_path: Path, tenant_id: UUID) -> None:
    """The wiring, not the projector.

    ``project_run`` was already covered with hand-built attempts — which is
    exactly why this gap survived: the projector worked, but this entry point
    never called it, so a real DAG run accumulated nothing and the graph's
    "which projects has this plugin been proven in?" stayed empty for it.
    """
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    seed_a = _write_seed(staging, "ledger-a.csv", _ledger_csv("A"))
    seed_b = _write_seed(staging, "ledger-b.csv", _ledger_csv("B"))
    service = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW))
    run_id: str | None = None
    try:
        result = service.start_plan_run(
            _build_plan(),
            seed_inputs={("ledger-a", "ledger"): seed_a, ("ledger-b", "ledger"): seed_b},
            worker_id="cw3-test-worker",
            staging_root=staging,
        )
        run_id = result["run_id"]
        assert result["status"] == "succeeded"

        with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.fetchone()
            cur.execute(
                """SELECT plugin_id, plugin_version FROM experience.node_observations
                   WHERE run_id=%s ORDER BY plugin_id""",
                (run_id,),
            )
            rows = cur.fetchall()
        assert rows, "a real DAG run left no node observation"
        assert {plugin for plugin, _ in rows} == {
            "audit.ledger-quality", "quant.experiment-evaluator",
        }
        assert all(version for _, version in rows), f"plugin_version must be recorded: {rows}"
    finally:
        if run_id is not None:
            _purge_experience(UUID(run_id), tenant_id)
