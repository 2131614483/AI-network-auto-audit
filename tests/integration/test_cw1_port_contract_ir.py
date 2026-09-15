"""CW1 contract tests: port contracts + deterministic compiler + port-bound execution IR.

Acceptance (方案 第13节 CW1 退出条件):
  - missing input / wrong contract / cycle / duplicate node_instance_id /
    duplicate port_id on same node / budget exceeded / unsupported field
    all have failing tests;
  - the same plugin appearing in multiple instances (distinct node_instance_id)
    is legal;
  - the same input reproduces the same execution hash, and layout changes do
    not change the hash.

Current defect (方案 P0): isolated.py keys inputs off a single ``previous_path``
(topological order only), which cannot express multi-input data flow. CW1
replaces that with an ExecutionPlan IR whose edges bind
``(node_instance_id, output_port_id) -> (node_instance_id, input_port_id)`` and
a runtime output index ``outputs[(node_instance_id, output_port_id)]``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import url2pathname
from uuid import UUID, uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput, IsolatedPluginRuntime
from packages.plugin_topology.compiler import CompileError, compile_plan
from packages.plugin_topology.ports_executor import PortBoundExecutor, register_adapter
from packages.policy.engine import PolicyEngine

_SHA64 = "a" * 64


# -- port / node / edge construction helpers ----------------------------------


def _port(port_id: str, direction: str, *, schema_ref: str = "artifact-ref", required: bool = True,
          cardinality: str = "one") -> dict:
    return {
        "port_id": port_id,
        "direction": direction,
        "schema_ref": schema_ref,
        "schema_version": "1.0.0",
        "schema_sha256": _SHA64,
        "media_type": "application/json",
        "required": required,
        "cardinality": cardinality,
        "classification": "internal",
        "transport": "artifact_ref",
    }


def _node(node_instance_id: str, capability: str, plugin_id: str, *,
          inputs: tuple[dict, ...] = (), outputs: tuple[dict, ...] = ()) -> dict:
    return {
        "node_instance_id": node_instance_id,
        "plugin_id": plugin_id,
        "capability": capability,
        "input_ports": list(inputs),
        "output_ports": list(outputs),
    }


def _edge(edge_id: str, source_instance: str, source_port: str,
          target_instance: str, target_port: str, *, adapter: str | None = None) -> dict:
    edge = {
        "edge_id": edge_id,
        "source_instance": source_instance,
        "source_port": source_port,
        "target_instance": target_instance,
        "target_port": target_port,
    }
    if adapter is not None:
        edge["adapter"] = adapter
    return edge


# -- contracts used by the tests -------------------------------------------------
# producer (verified): audit.ledger-quality emits audit-quality-candidates
# consumer (verified): quant.experiment-evaluator consumes a backtest-report
_LEDGER_IN = _port("ledger", "input", schema_ref="ledger-artifact-ref")
_LEDGER_OUT = _port("candidates", "output", schema_ref="audit-quality-candidates")
_EXPERIMENT_IN = _port("experiment", "input", schema_ref="backtest-report")
_EVAL_OUT = _port("evaluation", "output", schema_ref="experiment-evaluation")

_BUDGET = {"max_chain_length": 8, "max_candidates": 8, "max_latency_ms": 5000}

_ADAPTER = "candidates-to-backtest"


def _candidates_to_backtest(producer_path: Path, node_dir: Path, tenant_id: UUID) -> dict:
    """Test-registered explicit conversion: audit-quality-candidates ->
    backtest-report (deterministic, keeps source provenance in summary).

    Mirrors ``packages/plugin_topology/port_adapters.py``: provenance comes from
    the contract's ``ledger_sha256``, not a ``summary.quality_hash`` field the
    contract does not define.
    """
    candidates = json.loads(producer_path.read_text(encoding="utf-8-sig"))
    ledger_sha = str(candidates.get("ledger_sha256") or "").strip()
    if not ledger_sha:
        raise ValueError("candidates artifact carries no ledger_sha256")
    source_ref = f"ledger-quality:{ledger_sha[:16]}"
    report = {
        "contract_id": "backtest-report",
        "contract_version": "1.0.0",
        "backtest_id": "cw1-" + ledger_sha[:8],
        "strategy_key": "cw1-strategy",
        "strategy_version": "1.0.0",
        "code_sha256": _SHA64,
        "snapshot_sha256": _SHA64,
        "reference_time": "2026-01-05T00:00:00+00:00",
        "point_in_time_gate": "2026-01-05T00:00:00+00:00",
        "metrics": {
            "simulated_only": True,
            "total_return": 0.05,
            "annualized_return": 0.20,
            "volatility": 0.10,
            "sharpe": 1.50,
            "max_drawdown": -0.05,
            "win_rate": 0.60,
            "n_periods": 4,
        },
        "period_returns": [0.01, 0.02, -0.005, 0.03],
        "summary": {"simulated": True, "source_refs": [source_ref]},
    }
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    path = node_dir / f"backtest-{sha[:16]}.json"
    path.write_bytes(raw)
    # value bound under the target port id ("experiment")
    return {
        "report": {"uri": path.resolve().as_uri(), "sha256": sha, "size_bytes": len(raw)},
        "baseline": None,
    }


register_adapter(_ADAPTER, _candidates_to_backtest)


# -- compiler failure tests ----------------------------------------------------


def test_missing_required_input_rejected() -> None:
    nodes = [
        _node("consumer", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,)),
    ]
    with pytest.raises(CompileError, match="required input"):
        compile_plan(nodes=nodes, edges=[], budget=_BUDGET, plan_key="plan-1")


def test_unknown_port_rejected() -> None:
    nodes = [
        _node("producer", "audit.ledger.validate", "audit.ledger-quality",
              outputs=(_LEDGER_OUT,)),
        _node("consumer", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,)),
    ]
    edges = [
        _edge("e1", "producer", "no-such-port", "consumer", "experiment"),
    ]
    with pytest.raises(CompileError, match="port"):
        compile_plan(nodes=nodes, edges=edges, budget=_BUDGET, plan_key="plan-1")


def test_wrong_direction_rejected() -> None:
    nodes = [
        _node("producer", "audit.ledger.validate", "audit.ledger-quality",
              outputs=(_LEDGER_OUT,)),
        _node("consumer", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,)),
    ]
    # source must be an output port, so pointing at producer's input port fails
    bad = _edge("e1", "producer", "ledger", "consumer", "experiment")
    with pytest.raises(CompileError, match="output port"):
        compile_plan(nodes=nodes, edges=[bad], budget=_BUDGET, plan_key="plan-1")


def test_schema_mismatch_without_adapter_rejected() -> None:
    nodes = [
        _node("producer", "audit.ledger.validate", "audit.ledger-quality",
              outputs=(_LEDGER_OUT,)),
        _node("consumer", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,)),
    ]
    # ledger-quality emits audit-quality-candidates@1; consumer requires
    # backtest-report@1 -> contract mismatch, no adapter declared.
    edges = [_edge("e1", "producer", "candidates", "consumer", "experiment")]
    with pytest.raises(CompileError, match="contract"):
        compile_plan(nodes=nodes, edges=edges, budget=_BUDGET, plan_key="plan-1")


def test_cycle_rejected() -> None:
    nodes = [
        _node("a", "cap.a", "plugin-a", outputs=(_port("o", "output"),),
              inputs=(_port("i", "input"),)),
        _node("b", "cap.b", "plugin-b", outputs=(_port("o", "output"),),
              inputs=(_port("i", "input"),)),
    ]
    edges = [
        _edge("e1", "a", "o", "b", "i"),
        _edge("e2", "b", "o", "a", "i"),
    ]
    with pytest.raises(CompileError, match="cycle"):
        compile_plan(nodes=nodes, edges=edges, budget=_BUDGET, plan_key="plan-1")


def test_duplicate_node_instance_id_rejected() -> None:
    nodes = [
        _node("dup", "cap.a", "plugin-a", outputs=(_port("o", "output"),)),
        _node("dup", "cap.b", "plugin-b", inputs=(_port("i", "input"),)),
    ]
    with pytest.raises(CompileError, match="node_instance_id"):
        compile_plan(nodes=nodes, edges=[], budget=_BUDGET, plan_key="plan-1")


def test_duplicate_port_id_on_same_node_rejected() -> None:
    nodes = [
        _node("n", "cap.a", "plugin-a",
              inputs=(_port("dup", "input"), _port("dup", "input"))),
    ]
    with pytest.raises(CompileError, match="port_id"):
        compile_plan(nodes=nodes, edges=[], budget=_BUDGET, plan_key="plan-1")


def test_budget_exceeded_rejected() -> None:
    # chain head n0 is seeded (not a required-input violation); only the
    # max_chain_length budget may fire here.
    head_in = _port("i", "input", required=False)
    nodes = [
        _node("n0", "cap.0", "plugin-0", inputs=(head_in,), outputs=(_port("o", "output"),)),
        _node("n1", "cap.1", "plugin-1", inputs=(_port("i", "input"),), outputs=(_port("o", "output"),)),
        _node("n2", "cap.2", "plugin-2", inputs=(_port("i", "input"),), outputs=(_port("o", "output"),)),
    ]
    edges = [_edge("e1", "n0", "o", "n1", "i"), _edge("e2", "n1", "o", "n2", "i")]
    with pytest.raises(CompileError, match="budget"):
        compile_plan(nodes=nodes, edges=edges, budget={"max_chain_length": 2}, plan_key="plan-1")


def test_unsupported_field_rejected() -> None:
    nodes = [
        _node("n", "cap.a", "plugin-a", outputs=(_port("o", "output"),)),
    ]
    # unknown field on a node (e.g. a join rule the compiler does not support)
    nodes[0]["join"] = {"type": "full"}
    with pytest.raises(CompileError, match="unsupported field"):
        compile_plan(nodes=nodes, edges=[], budget=_BUDGET, plan_key="plan-1")


# -- legal compilation ---------------------------------------------------------


def test_same_plugin_multi_instance_legal() -> None:
    nodes = [
        _node("ledger-a", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,)),
        _node("ledger-b", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,)),
    ]
    plan = compile_plan(
        nodes=nodes, edges=[], budget=_BUDGET, plan_key="plan-1",
        seed_inputs={("ledger-a", "ledger"), ("ledger-b", "ledger")},
    )
    assert {n.node_instance_id for n in plan.nodes} == {"ledger-a", "ledger-b"}


def test_fan_out_legal() -> None:
    nodes = [
        _node("producer", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,)),
        _node("consumer-x", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,)),
        _node("consumer-y", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,)),
    ]
    edges = [
        _edge("e1", "producer", "candidates", "consumer-x", "experiment", adapter=_ADAPTER),
        _edge("e2", "producer", "candidates", "consumer-y", "experiment", adapter=_ADAPTER),
    ]
    plan = compile_plan(
        nodes=nodes, edges=edges, budget=_BUDGET, plan_key="plan-1",
        seed_inputs={("producer", "ledger")},
    )
    assert len(plan.edges) == 2


def test_fan_in_requires_list_cardinality() -> None:
    single = _port("i", "input", required=True, cardinality="one")
    many = _port("i", "input", required=True, cardinality="many")
    producers = [
        _node("p-a", "cap.a", "plugin-a", outputs=(_port("o", "output"),)),
        _node("p-b", "cap.b", "plugin-b", outputs=(_port("o", "output"),)),
    ]
    consumer_one = [
        _node("c", "cap.c", "plugin-c", inputs=(single,)),
    ]
    edges = [
        _edge("e1", "p-a", "o", "c", "i"),
        _edge("e2", "p-b", "o", "c", "i"),
    ]
    with pytest.raises(CompileError, match="fan-in"):
        compile_plan(nodes=[*producers, *consumer_one], edges=edges, budget=_BUDGET, plan_key="plan-1")

    consumer_many = [
        _node("c", "cap.c", "plugin-c", inputs=(many,)),
    ]
    plan = compile_plan(nodes=[*producers, *consumer_many], edges=edges, budget=_BUDGET, plan_key="plan-2")
    assert len(plan.edges) == 2


# -- deterministic hash --------------------------------------------------------


def test_execution_hash_reproducible_and_layout_independent() -> None:
    nodes = [
        _node("producer", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,)),
        _node("consumer", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,)),
    ]
    edges = [
        _edge("e1", "producer", "candidates", "consumer", "experiment", adapter=_ADAPTER),
    ]
    a = compile_plan(nodes=nodes, edges=edges, budget=_BUDGET, plan_key="plan-1",
                     layout={"x": 1, "y": 2}, seed_inputs={("producer", "ledger")})
    b = compile_plan(nodes=nodes, edges=edges, budget=_BUDGET, plan_key="plan-1",
                     layout={"x": 999, "y": -5, "zoom": 2.5}, seed_inputs={("producer", "ledger")})
    assert a.execution_hash == b.execution_hash
    # and the hash is deterministic for byte-identical input
    c = compile_plan(nodes=nodes, edges=edges, budget=_BUDGET, plan_key="plan-1",
                     layout={"x": 1, "y": 2}, seed_inputs={("producer", "ledger")})
    assert a.execution_hash == c.execution_hash
    assert len(a.execution_hash) == 64


# -- port-bound execution (real runner, staging artifacts) ----------------------


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
        # fan-out: both consumers bind the SAME producer port (ledger-a)
        _edge("e1", "ledger-a", "candidates", "consumer-x", "experiment", adapter=_ADAPTER),
        _edge("e2", "ledger-a", "candidates", "consumer-y", "experiment", adapter=_ADAPTER),
    ]
    return compile_plan(
        nodes=nodes, edges=edges, budget=_BUDGET, plan_key="plan-cw1",
        seed_inputs={("ledger-a", "ledger"), ("ledger-b", "ledger")},
    )


def test_port_bound_executor_resolves_inputs_by_port(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    seed_a = _write_seed(staging, "ledger-a.csv", _ledger_csv("A"))
    seed_b = _write_seed(staging, "ledger-b.csv", _ledger_csv("B"))
    plan = _build_plan()
    executor = PortBoundExecutor(
        plan=plan,
        runtime=IsolatedPluginRuntime(allowed_roots=(staging,)),
        policy=PolicyEngine(allow=["audit.ledger.validate", "quant.experiment.evaluate"]),
        tenant_id=uuid4(),
        trace_id=str(uuid4()),
        staging_root=staging,
        idempotency_key="cw1-exec-1",
        seed_inputs={("ledger-a", "ledger"): seed_a, ("ledger-b", "ledger"): seed_b},
    )
    outcome = executor.execute()
    assert outcome["status"] == "succeeded"
    assert outcome["plan_key"] == "plan-cw1"
    assert outcome["execution_hash"] == plan.execution_hash

    # fan-out: consumer-x and consumer-y both read ledger-a's candidates port,
    # so both inputs resolve to the same producer artifact sha.
    outputs = {o["node_instance_id"]: o for o in outcome["outputs"]}
    assert "ledger-a" in outputs
    by_port = {o["port_id"]: o["sha256"] for o in outputs["ledger-a"]["ports"]}
    assert "candidates" in by_port
    producer_sha = by_port["candidates"]

    consumers = [e for e in outcome["entries"] if e["node_instance_id"].startswith("consumer-")]
    assert len(consumers) == 2
    for entry in consumers:
        assert entry["status"] == "succeeded", entry
        bound = {b["port_id"]: b for b in entry["input_bindings"]}
        assert bound["experiment"]["source_instance"] == "ledger-a"
        assert bound["experiment"]["source_port"] == "candidates"
        assert bound["experiment"]["sha256"] == producer_sha
    # deterministic adapter -> identical consumer output for identical input
    x = next(e for e in consumers if e["node_instance_id"] == "consumer-x")
    y = next(e for e in consumers if e["node_instance_id"] == "consumer-y")
    assert x["outputs"][0]["sha256"] == y["outputs"][0]["sha256"]
    # and every output artifact exists on disk (data-layer drill-down)
    for entry in consumers:
        for out in entry["outputs"]:
            uri = out["uri"]
            p = Path(url2pathname(uri[len("file://"):] if uri.startswith("file://") else uri))
            assert p.is_file(), uri
            assert hashlib.sha256(p.read_bytes()).hexdigest() == out["sha256"]


def test_port_bound_executor_uses_previous_path_semantics_never(tmp_path: Path) -> None:
    """Regression guard: the executor must key inputs off (instance, port), never
    'the previous node in topological order' (方案 P0)."""
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    seed_a = _write_seed(staging, "ledger-a.csv", _ledger_csv("A"))
    seed_b = _write_seed(staging, "ledger-b.csv", _ledger_csv("B"))

    nodes = [
        _node("producer-a", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_LEDGER_IN,), outputs=(_LEDGER_OUT,)),
        _node("consumer", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_EXPERIMENT_IN,), outputs=(_EVAL_OUT,)),
    ]
    # consumer binds producer-a's port; producer-b is NOT part of the plan at all
    edges = [
        _edge("e1", "producer-a", "candidates", "consumer", "experiment", adapter=_ADAPTER),
    ]
    plan = compile_plan(nodes=nodes, edges=edges, budget=_BUDGET, plan_key="plan-cw1b",
                        seed_inputs={("producer-a", "ledger"), ("unused", "ledger")})
    executor = PortBoundExecutor(
        plan=plan,
        runtime=IsolatedPluginRuntime(allowed_roots=(staging,)),
        policy=PolicyEngine(allow=["audit.ledger.validate", "quant.experiment.evaluate"]),
        tenant_id=uuid4(), trace_id=str(uuid4()), staging_root=staging,
        idempotency_key="cw1-exec-2",
        seed_inputs={("producer-a", "ledger"): seed_a, ("unused", "ledger"): seed_b},
    )
    outcome = executor.execute()
    assert outcome["status"] == "succeeded", outcome
    entry = outcome["entries"][1]
    assert entry["status"] == "succeeded", entry
    bound = entry["input_bindings"][0]
    assert bound["source_instance"] == "producer-a"
    # the bound artifact is producer-a's candidates output (not the raw seed);
    # recompute the sha from disk and confirm the payload embeds seed A's hash
    p = Path(url2pathname(bound["uri"][len("file://"):]))
    assert hashlib.sha256(p.read_bytes()).hexdigest() == bound["sha256"]
    content = json.loads(p.read_text(encoding="utf-8-sig"))
    assert content["ledger_sha256"] == seed_a.sha256
