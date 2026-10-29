"""A compiled node must not manufacture its own execution permission."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from packages.ai_planner.catalog import PORT_CONTRACTS
from packages.plugin_runtime import runner
from packages.plugin_runtime.runner import ArtifactInput, IsolatedPluginRuntime
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.ports_executor import PortBoundExecutor
from packages.policy.engine import PolicyEngine

CAPABILITY = "audit.ledger.validate"


def _executor(tmp_path: Path, policy: PolicyEngine, *, copies: int = 1) -> PortBoundExecutor:
    tenant_id = uuid4()
    source = tmp_path / "ledger.csv"
    raw = (
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "V-01,2026-01-05,1001,debit,100.00,0.00\n"
        "V-01,2026-01-05,6001,credit,0.00,100.00\n"
    ).encode("utf-8")
    source.write_bytes(raw)
    artifact = ArtifactInput(
        artifact_id=uuid4(), tenant_id=tenant_id, uri=source.resolve().as_uri(),
        media_type="text/csv", sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="internal", period="2026-01",
    )
    nodes = [
        {
            "node_instance_id": f"ledger-{index}",
            "plugin_id": "audit.ledger-quality",
            "capability": CAPABILITY,
            "input_ports": [{**PORT_CONTRACTS["ledger"], "port_id": "ledger", "direction": "input"}],
            "output_ports": [
                {**PORT_CONTRACTS["candidates"], "port_id": "candidates", "direction": "output"},
            ],
        }
        for index in range(copies)
    ]
    seeds = {(node["node_instance_id"], "ledger"): artifact for node in nodes}
    plan = compile_plan(
        nodes=nodes, edges=[], plan_key="policy-regression", seed_inputs=set(seeds),
    )
    return PortBoundExecutor(
        plan=plan, runtime=IsolatedPluginRuntime(allowed_roots=(tmp_path,)),
        policy=policy, tenant_id=tenant_id, trace_id=str(uuid4()),
        staging_root=tmp_path, idempotency_key="policy-regression", seed_inputs=seeds,
    )


@pytest.mark.parametrize(
    "policy",
    [
        PolicyEngine(allow=[CAPABILITY], deny=[CAPABILITY]),
        PolicyEngine(allow=[CAPABILITY], rules=[
            {"rule_id": "freeze-ledger", "effect": "freeze", "match": {"capabilities": [CAPABILITY]}},
        ]),
        PolicyEngine(allow=[CAPABILITY], rules=[
            {"rule_id": "review-ledger", "effect": "require_approval", "match": {"capabilities": [CAPABILITY]}},
        ]),
        PolicyEngine(allow=["topology.chain.execute", "topology.chain.execute.isolated"]),
    ],
    ids=["deny-overrides-allow", "freeze", "approval-required", "chain-grant-is-not-node-grant"],
)
def test_node_without_permission_never_starts_a_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine,
) -> None:
    child = Mock(side_effect=AssertionError("a blocked node must never start a child"))
    monkeypatch.setattr(runner, "run", child)
    executor = _executor(tmp_path, policy)

    outcome = executor.execute()

    child.assert_not_called()
    assert outcome["status"] == "failed"
    entry = outcome["entries"][0]
    assert entry["status"] == "failed"
    assert "PluginPolicyDenied" in entry["error"]
    assert entry["outputs"] == []
    assert entry["trace_id"] == executor.trace_id
    assert all(not node["ports"] for node in outcome["outputs"])
    assert list(tmp_path.rglob("output-*.json")) == []


def test_node_with_a_matching_caller_rule_executes_and_keeps_provenance(tmp_path: Path) -> None:
    policy = PolicyEngine(rules=[{
        "rule_id": "local-ledger-read", "effect": "allow",
        "match": {
            "capabilities": [CAPABILITY], "risk_classes": ["read_only"],
            "side_effects": ["read_only"],
            "argument_equals": {"plugin_id": "audit.ledger-quality"},
        },
    }])
    executor = _executor(tmp_path, policy)

    outcome = executor.execute()

    assert outcome["status"] == "succeeded", outcome
    entry = outcome["entries"][0]
    assert entry["trace_id"] == executor.trace_id
    assert entry["input_sha256"] == next(iter(executor.seed_inputs.values())).sha256
    assert entry["plugin_id"] == "audit.ledger-quality"
    assert len(entry["runtime_code_sha256"]) == 64
    assert entry["outputs"][0]["sha256"] == entry["output_checksum"]


def test_each_node_rechecks_the_caller_policy_after_a_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyEngine(allow=[CAPABILITY])
    evaluate = policy.evaluate

    def revoke_after_first_check(*args, **kwargs):
        result = evaluate(*args, **kwargs)
        policy.deny = (CAPABILITY,)
        return result

    monkeypatch.setattr(policy, "evaluate", revoke_after_first_check)
    executor = _executor(tmp_path, policy, copies=2)

    outcome = executor.execute()

    assert outcome["status"] == "failed"
    assert [entry["status"] for entry in outcome["entries"]] == ["succeeded", "failed"]
    assert "PluginPolicyDenied" in outcome["entries"][1]["error"]
    assert outcome["entries"][1]["outputs"] == []
