"""Regression tests for AI-authored identifiers that reach the filesystem.

A planner drafts ``plan_key`` / ``node_instance_id`` from a natural-language
goal and those strings become path segments under the staging root
(``staging_root / "chains" / plan_key / instance_id``).  Before the compiler
gated them, ``plan-../../../../pwned`` compiled cleanly and the executor then
``mkdir``-ed outside the sandbox.  These tests pin the gate at both layers:
the compile-time rejection and the executor's resolved-containment fallback.

Also covers the two provenance defects found alongside it: a compiled plan
losing its seed injection points, and the adapter reading a contract field
that does not exist.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import url2pathname
from uuid import uuid4

import pytest

from packages.ai_planner.catalog import PORT_CONTRACTS
from packages.plugin_topology.compiler import CompileError, compile_plan
from packages.plugin_topology.ir import ExecutionPlan
from packages.plugin_topology.ports_executor import PortBoundExecutor
from packages.policy.engine import PolicyEngine

_TRAVERSALS = (
    "plan-../../../../pwned",
    "../../etc/passwd",
    "plan-a/b",
    "plan-a\\b",
    "..",
    ".",
    "/absolute",
    "C:/windows",
    "plan-a/../../../x",
    "plan- ",
    "plan-a.",
    "plan-" + "x" * 200,
)


def _port(port_id: str, direction: str) -> dict:
    contract = dict(PORT_CONTRACTS[port_id])
    contract.update({"port_id": port_id, "direction": direction})
    return contract


def _nodes(instance_id: str = "n1") -> list[dict]:
    return [
        {
            "node_instance_id": instance_id,
            "plugin_id": "audit.ledger-quality",
            "capability": "audit.ledger.validate",
            "input_ports": [_port("ledger", "input")],
            "output_ports": [_port("candidates", "output")],
        }
    ]


def _compile(**overrides: object) -> ExecutionPlan:
    kwargs: dict = {
        "nodes": _nodes(),
        "edges": [],
        "plan_key": "plan-safe",
        "seed_inputs": {("n1", "ledger")},
    }
    kwargs.update(overrides)
    return compile_plan(**kwargs)  # type: ignore[arg-type]


# --- compile-time gate -------------------------------------------------------


@pytest.mark.parametrize("plan_key", _TRAVERSALS)
def test_traversal_plan_key_is_rejected(plan_key: str) -> None:
    with pytest.raises(CompileError, match="plan_key"):
        _compile(plan_key=plan_key)


@pytest.mark.parametrize("instance_id", _TRAVERSALS)
def test_traversal_node_id_is_rejected(instance_id: str) -> None:
    with pytest.raises(CompileError, match="node_instance_id"):
        _compile(nodes=_nodes(instance_id), seed_inputs={(instance_id, "ledger")})


@pytest.mark.parametrize("edge_id", _TRAVERSALS)
def test_traversal_edge_id_is_rejected(edge_id: str) -> None:
    nodes = [
        *_nodes("producer-a"),
        {
            "node_instance_id": "consumer",
            "plugin_id": "quant.experiment-evaluator",
            "capability": "quant.experiment.evaluate",
            "input_ports": [_port("experiment", "input")],
            "output_ports": [_port("evaluation", "output")],
        },
    ]
    with pytest.raises(CompileError, match="edge_id"):
        _compile(
            nodes=nodes,
            edges=[
                {
                    "edge_id": edge_id,
                    "source_instance": "producer-a",
                    "source_port": "candidates",
                    "target_instance": "consumer",
                    "target_port": "experiment",
                    "adapter": "candidates-to-backtest",
                }
            ],
        )


def test_safe_identifiers_still_compile() -> None:
    """The gate must not be so tight that legitimate ids break."""
    plan = _compile(
        plan_key="plan-ai-fund-fraud-100",
        nodes=_nodes("ledger_a.1"),
        seed_inputs={("ledger_a.1", "ledger")},
    )
    assert plan.plan_key == "plan-ai-fund-fraud-100"
    assert plan.nodes[0].node_instance_id == "ledger_a.1"


def test_missing_or_empty_plan_key_is_rejected() -> None:
    with pytest.raises(CompileError, match="plan_key"):
        _compile(plan_key="")


# --- executor containment fallback -------------------------------------------


def test_executor_refuses_a_plan_that_bypassed_the_compiler(tmp_path: Path) -> None:
    """Defence in depth: plans can arrive from the DB, not only compile_plan."""
    plan = _compile()
    object.__setattr__(plan, "plan_key", "plan-../../../../pwned")
    executor = PortBoundExecutor(
        plan=plan,
        runtime=None,  # type: ignore[arg-type]  # never reached: guard fires first
        policy=PolicyEngine(allow=["audit.ledger.validate"]),
        tenant_id=uuid4(),
        trace_id=str(uuid4()),
        staging_root=tmp_path,
        idempotency_key="traversal-probe",
        seed_inputs={},
    )
    with pytest.raises(ValueError, match="plan_key"):
        executor._node_dir("n1")
    # nothing was created outside, or inside, the staging root
    assert list(tmp_path.rglob("*")) == []


def test_executor_node_dir_stays_inside_the_staging_root(tmp_path: Path) -> None:
    executor = PortBoundExecutor(
        plan=_compile(plan_key="plan-safe"),
        runtime=None,  # type: ignore[arg-type]
        policy=PolicyEngine(allow=["audit.ledger.validate"]),
        tenant_id=uuid4(),
        trace_id=str(uuid4()),
        staging_root=tmp_path,
        idempotency_key="containment",
        seed_inputs={},
    )
    node_dir = executor._node_dir("n1")
    assert node_dir.is_relative_to(tmp_path.resolve())
    assert node_dir == (tmp_path / "chains" / "plan-safe" / "n1").resolve()


# --- seed injection points survive compilation -------------------------------


def test_compiled_plan_carries_its_seed_injection_points() -> None:
    """A caller must not have to guess the node names the planner chose."""
    plan = _compile(nodes=_nodes("ledger_validation"), seed_inputs={("ledger_validation", "ledger")})
    assert plan.seed_keys() == (("ledger_validation", "ledger"),)
    assert plan.seed_keys("ledger") == (("ledger_validation", "ledger"),)
    assert plan.seed_keys("experiment") == ()


def test_seed_keys_are_sorted_and_filterable() -> None:
    plan = _compile(
        nodes=[{**_nodes("b")[0], "node_instance_id": "b"}, {**_nodes("a")[0], "node_instance_id": "a"}],
        seed_inputs={("b", "ledger"), ("a", "ledger")},
    )
    assert plan.seed_keys() == (("a", "ledger"), ("b", "ledger"))


def test_seed_inputs_do_not_change_the_execution_hash() -> None:
    """Folding injection points into the hash would invalidate stored hashes."""
    optional = [{**_nodes("n1")[0], "input_ports": [{**_port("ledger", "input"), "required": False}]}]
    without = _compile(nodes=optional, seed_inputs=None)
    with_seed = _compile(nodes=optional, seed_inputs={("n1", "ledger")})
    assert without.seed_inputs == frozenset()
    assert with_seed.seed_inputs == frozenset({("n1", "ledger")})
    assert without.execution_hash == with_seed.execution_hash


# --- adapter provenance ------------------------------------------------------

_CANDIDATES_TEMPLATE = {
    "contract_id": "audit-quality-candidates",
    "contract_version": "1.0.0",
    "ledger_sha256": "b" * 64,
    "schema_mapping_version": "1.0.0",
    "period": "2026-01",
    "rule_pack_sha256": "c" * 64,
    "summary": {"total_rows": 9, "candidate_count": 6},
    "candidates": [],
}


def _write_candidates(tmp_path: Path, **overrides: object) -> Path:
    payload = {**_CANDIDATES_TEMPLATE, **overrides}
    for key, value in list(overrides.items()):
        if value is None:
            payload.pop(key, None)
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_adapter_derives_provenance_from_the_contract_field(tmp_path: Path) -> None:
    """``summary.quality_hash`` does not exist; ``ledger_sha256`` does."""
    from packages.plugin_topology.port_adapters import _candidates_to_backtest

    producer = _write_candidates(tmp_path)
    result = _candidates_to_backtest(producer, tmp_path, uuid4())
    report_uri = result["report"]["uri"]
    written = json.loads(Path(url2pathname(report_uri[len("file://"):])).read_text(encoding="utf-8"))
    provenance = written["summary"]["source_refs"][0]
    assert provenance.startswith("ledger-quality:bbbbbbbbbbbbbbbb")
    assert "unknown" not in provenance
    assert "rules:cccccccccccccccc" in provenance
    assert written["backtest_id"] == "bt-bbbbbbbb"


def test_adapter_refuses_to_fabricate_provenance(tmp_path: Path) -> None:
    from packages.plugin_topology.port_adapters import _candidates_to_backtest

    producer = _write_candidates(tmp_path, ledger_sha256=None)
    with pytest.raises(ValueError, match="ledger_sha256"):
        _candidates_to_backtest(producer, tmp_path, uuid4())


def test_adapter_rejects_a_wrong_contract(tmp_path: Path) -> None:
    from packages.plugin_topology.port_adapters import _candidates_to_backtest

    producer = _write_candidates(tmp_path, contract_id="something-else")
    with pytest.raises(ValueError, match="expects a audit-quality-candidates"):
        _candidates_to_backtest(producer, tmp_path, uuid4())
