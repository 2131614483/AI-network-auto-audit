"""Unit tests for Plugin Topology M3: invocation chains, intents and adapters.

Pure, database-free coverage: deterministic chain derivation (same locked plan
-> same chain_key/checksum/order/bindings), contract/bridge port wiring, cycle
rejection, the migration-0039 seed checksum reproduction, per-node intent
decisions, and the read-only adapter contracts.  Nothing here executes a
plugin; every projection stays ``plan_only``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from packages.plugin_topology.adapters import (
    agent_adapter_contract,
    policy_adapter_contract,
    runtime_adapter_contract,
)
from packages.plugin_topology.chain import (
    ChainResolver,
    IntentGenerator,
    InvocationChain,
    InvocationIntent,
)
from packages.plugin_topology.contracts import ChainMaterialize
from packages.plugin_topology.planner import PlannerCycleError

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"

# -- seeds that mirror migration 0039 ---------------------------------------

_SEED_PLAN_KEY = "plan-" + hashlib.sha256(b"ledger-quality-slot|research-note-slot").hexdigest()[:16]
_SEED_VERSION = "1.0.0"

_SEED_NODES = [
    {"slot_key": "audit.ledger.validate", "capability": "audit.ledger.validate",
     "blueprint_key": "ledger-quality-slot", "alternatives": []},
    {"slot_key": "quant.research-note.draft", "capability": "quant.research-note.draft",
     "blueprint_key": "research-note-slot", "alternatives": []},
]
_SEED_EDGES = [
    {"source_node": "ledger-quality-slot", "target_node": "research-note-slot", "relation_type": "bridge"},
]
_SEED_BLUEPRINTS = {
    "ledger-quality-slot": {
        "capability": "audit.ledger.validate", "version": "1.0.0",
        "inputs": [], "outputs": ["audit-quality-candidates"],
    },
    "research-note-slot": {
        "capability": "quant.research-note.draft", "version": "1.0.0",
        "inputs": ["contracts/jsonschema/audit-quality-candidates@1"], "outputs": ["research-note-draft"],
    },
}
_SEED_BRIDGES = {
    "ledger-quality-slot": {
        "ref_kind": "capability_contract",
        "bridge_ref": "contracts/jsonschema/audit-quality-candidates@1",
    },
}


def _seed_chain(trace_id: str = "") -> InvocationChain:
    return ChainResolver().materialize(
        plan_key=_SEED_PLAN_KEY,
        plan_nodes=_SEED_NODES,
        plan_edges=_SEED_EDGES,
        blueprints=_SEED_BLUEPRINTS,
        bridges=_SEED_BRIDGES,
        planner_version=_SEED_VERSION,
        release_locked=True,
        trace_id=trace_id,
    )


def _chain_checksum_reference(chain: InvocationChain) -> str:
    """Reference digest formula (independent re-implementation for the test).

    Structural clone of migration 0039 ``_chain_checksum`` so the service
    formula is compared against a second, hand-written implementation.
    """
    payload = {
        "chain_key": chain.chain_key,
        "plan_key": chain.plan_key,
        "mode": chain.mode,
        "chain_order": [
            {"slot_key": n.slot_key, "blueprint_key": n.blueprint_key,
             "capability": n.capability, "ordinal": n.ordinal, "role": n.role}
            for n in chain.chain_order
        ],
        "fallbacks": [
            {"slot_key": f.slot_key, "blueprint_key": f.blueprint_key,
             "order": f.order, "reason": f.reason}
            for f in chain.fallbacks
        ],
        "port_bindings": [
            {"producer_slot": b.producer_slot, "consumer_slot": b.consumer_slot,
             "relation_type": b.relation_type, "bind_mode": b.bind_mode,
             "contract_ref": b.contract_ref, "payload_disallowed": b.payload_disallowed}
            for b in chain.port_bindings
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# -- determinism -------------------------------------------------------------


def test_chain_materialize_is_deterministic() -> None:
    first = _seed_chain(trace_id="00000000-0000-0000-0000-0000000000a1")
    second = _seed_chain(trace_id="00000000-0000-0000-0000-0000000000b2")
    assert first.chain_key == second.chain_key
    assert first.checksum == second.checksum
    assert [n.blueprint_key for n in first.chain_order] == [n.blueprint_key for n in second.chain_order]
    assert [asdict(b) for b in first.port_bindings] == [asdict(b) for b in second.port_bindings]
    assert first.trace_id == "00000000-0000-0000-0000-0000000000a1"


def test_chain_reproduces_migration_seed_checksum() -> None:
    """The in-process ChainResolver yields the canonical seed digest verbatim."""
    chain = _seed_chain()
    assert chain.chain_key == "chain-" + hashlib.sha256(
        "|".join([_SEED_PLAN_KEY, "ledger-quality-slot", "research-note-slot"]).encode("utf-8")
    ).hexdigest()[:16]
    assert chain.checksum == _chain_checksum_reference(chain)
    assert chain.mode == "plan_only"
    assert chain.release_locked is True
    assert [n.blueprint_key for n in chain.chain_order] == ["ledger-quality-slot", "research-note-slot"]
    assert chain.port_bindings[0].bind_mode == "bridge_ref"
    assert chain.port_bindings[0].payload_disallowed is True


def test_chain_topological_order_tie_breaks_by_slot_key() -> None:
    resolver = ChainResolver()
    nodes = [
        {"slot_key": "zz.slot", "capability": "audit.z", "blueprint_key": "bp-z", "alternatives": []},
        {"slot_key": "aa.slot", "capability": "audit.a", "blueprint_key": "bp-a", "alternatives": []},
    ]
    chain = resolver.materialize(
        plan_key=_SEED_PLAN_KEY, plan_nodes=nodes, plan_edges=[],
        blueprints={"bp-z": _SEED_BLUEPRINTS["ledger-quality-slot"], "bp-a": _SEED_BLUEPRINTS["research-note-slot"]},
        bridges={}, planner_version=_SEED_VERSION,
    )
    # no edges => ready set contains both; slot_key decides the order deterministically
    assert [n.slot_key for n in chain.chain_order] == ["aa.slot", "zz.slot"]
    assert [n.ordinal for n in chain.chain_order] == [0, 1]


# -- port bindings and rejection paths ---------------------------------------


def test_chain_contract_binding_wires_shared_item_and_blocks_payloads() -> None:
    nodes = [
        {"slot_key": "produce", "capability": "audit.produce", "blueprint_key": "producer", "alternatives": []},
        {"slot_key": "consume", "capability": "audit.consume", "blueprint_key": "consumer", "alternatives": []},
    ]
    edges = [{"source_node": "producer", "target_node": "consumer", "relation_type": "data_flow"}]
    blueprints = {
        "producer": {"capability": "audit.produce", "version": "1.1.0", "inputs": [], "outputs": ["shared-item", "private-item"]},
        "consumer": {"capability": "audit.consume", "version": "1.0.0", "inputs": ["shared-item"], "outputs": []},
    }
    chain = ChainResolver().materialize(
        plan_key=_SEED_PLAN_KEY, plan_nodes=nodes, plan_edges=edges,
        blueprints=blueprints, bridges={}, planner_version=_SEED_VERSION,
    )
    binding = chain.port_bindings[0]
    assert binding.producer_slot == "produce"
    assert binding.consumer_slot == "consume"
    assert binding.relation_type == "data_flow"
    assert binding.bind_mode == "contract_ref"
    assert binding.contract_ref == "shared-item"
    assert binding.payload_disallowed is True


def test_chain_bridge_binding_uses_public_ref_only() -> None:
    chain = _seed_chain()
    binding = chain.port_bindings[0]
    assert binding.relation_type == "bridge"
    assert binding.bind_mode == "bridge_ref"
    assert binding.contract_ref == "contracts/jsonschema/audit-quality-candidates@1"


def test_chain_rejects_bridge_edge_without_registered_bridge() -> None:
    with pytest.raises(ValueError, match="requires a registered public bridge"):
        ChainResolver().materialize(
            plan_key=_SEED_PLAN_KEY, plan_nodes=_SEED_NODES, plan_edges=_SEED_EDGES,
            blueprints=_SEED_BLUEPRINTS, bridges={}, planner_version=_SEED_VERSION,
        )


def test_chain_rejects_invalid_bridge_ref_kind() -> None:
    with pytest.raises(ValueError, match="invalid public bridge"):
        ChainResolver().materialize(
            plan_key=_SEED_PLAN_KEY, plan_nodes=_SEED_NODES, plan_edges=_SEED_EDGES,
            blueprints=_SEED_BLUEPRINTS,
            bridges={"ledger-quality-slot": {"ref_kind": "domain_private_table", "bridge_ref": "schema://x"}},
            planner_version=_SEED_VERSION,
        )


def test_chain_rejects_no_shared_contract_item() -> None:
    nodes = [
        {"slot_key": "produce", "capability": "audit.produce", "blueprint_key": "producer", "alternatives": []},
        {"slot_key": "consume", "capability": "audit.consume", "blueprint_key": "consumer", "alternatives": []},
    ]
    edges = [{"source_node": "producer", "target_node": "consumer", "relation_type": "depends_on"}]
    blueprints = {
        "producer": {"capability": "audit.produce", "version": "1.0.0", "inputs": [], "outputs": ["a"]},
        "consumer": {"capability": "audit.consume", "version": "1.0.0", "inputs": ["b"], "outputs": []},
    }
    with pytest.raises(ValueError, match="no shared contract item"):
        ChainResolver().materialize(
            plan_key=_SEED_PLAN_KEY, plan_nodes=nodes, plan_edges=edges,
            blueprints=blueprints, bridges={}, planner_version=_SEED_VERSION,
        )


def test_chain_rejects_contract_version_mismatch() -> None:
    nodes = [
        {"slot_key": "produce", "capability": "audit.produce", "blueprint_key": "producer", "alternatives": []},
        {"slot_key": "consume", "capability": "audit.consume", "blueprint_key": "consumer", "alternatives": []},
    ]
    edges = [{"source_node": "producer", "target_node": "consumer", "relation_type": "depends_on"}]
    blueprints = {
        "producer": {"capability": "audit.produce", "version": "2.0.0", "inputs": [], "outputs": ["x"]},
        "consumer": {"capability": "audit.consume", "version": "1.5.0", "inputs": ["x"], "outputs": []},
    }
    with pytest.raises(ValueError, match="contract version mismatch"):
        ChainResolver().materialize(
            plan_key=_SEED_PLAN_KEY, plan_nodes=nodes, plan_edges=edges,
            blueprints=blueprints, bridges={}, planner_version=_SEED_VERSION,
        )


def test_chain_rejects_cycles() -> None:
    nodes = [
        {"slot_key": s, "capability": f"audit.cycle.{i}", "blueprint_key": b, "alternatives": []}
        for i, (s, b) in enumerate([("n0", "bp-n0"), ("n1", "bp-n1")])
    ]
    edges = [
        {"source_node": "bp-n0", "target_node": "bp-n1", "relation_type": "depends_on"},
        {"source_node": "bp-n1", "target_node": "bp-n0", "relation_type": "data_flow"},
    ]
    with pytest.raises(PlannerCycleError, match="cycle"):
        ChainResolver().materialize(
            plan_key=_SEED_PLAN_KEY, plan_nodes=nodes, plan_edges=edges,
            blueprints={"bp-n0": _SEED_BLUEPRINTS["ledger-quality-slot"], "bp-n1": _SEED_BLUEPRINTS["research-note-slot"]},
            bridges={}, planner_version=_SEED_VERSION,
        )


# -- intent projection --------------------------------------------------------


def test_intent_generator_projects_contract_bound_inputs_in_chain_order() -> None:
    chain = _seed_chain()
    intents = IntentGenerator().intents(chain, _SEED_BLUEPRINTS)
    assert [intent.slot_key for intent in intents] == ["audit.ledger.validate", "quant.research-note.draft"]
    ledger, research = intents
    assert ledger.expected_inputs == ()
    assert ledger.expected_outputs == ("audit-quality-candidates",)
    # the downstream slot receives exactly the wired bridge/contract reference
    assert research.expected_inputs == ("contracts/jsonschema/audit-quality-candidates@1",)
    assert research.expected_outputs == ("research-note-draft",)
    for intent in intents:
        assert intent.role == "primary"
        assert intent.policy_decision == "allowed"
        assert intent.status == "policy_allowed"


def test_intent_generator_derives_inputs_from_wired_bindings() -> None:
    """expected_inputs carry exactly the wired upstream contract refs, ordered."""
    nodes = [
        {"slot_key": "producer", "capability": "audit.produce", "blueprint_key": "producer-bp", "alternatives": []},
        {"slot_key": "sink", "capability": "audit.sink", "blueprint_key": "sink-bp", "alternatives": []},
    ]
    edges = [{"source_node": "producer-bp", "target_node": "sink-bp", "relation_type": "data_flow"}]
    blueprints = {
        "producer-bp": {"capability": "audit.produce", "version": "1.0.0", "inputs": [], "outputs": ["z", "a", "m"]},
        "sink-bp": {"capability": "audit.sink", "version": "1.0.0", "inputs": ["z", "a", "m"], "outputs": []},
    }
    chain = ChainResolver().materialize(
        plan_key=_SEED_PLAN_KEY, plan_nodes=nodes, plan_edges=edges,
        blueprints=blueprints, bridges={}, planner_version=_SEED_VERSION,
    )
    intents = IntentGenerator().intents(chain, blueprints)
    assert intents[0].expected_inputs == ()
    # the wired ref is the sorted-shared first item: inputs z/a/m intersect
    # producer outputs z/a/m => shared sorted = a,m,z => bound ref is "a"
    assert intents[1].expected_inputs == ("a",)


def test_intent_generator_applies_decision_overrides() -> None:
    chain = _seed_chain()
    intents = IntentGenerator().intents(
        chain, _SEED_BLUEPRINTS,
        decisions={
            "audit.ledger.validate": ("denied", "denied"),
            "quant.research-note.draft": ("requires_approval", "materialized"),
        },
    )
    assert {intent.slot_key: intent.policy_decision for intent in intents} == {
        "audit.ledger.validate": "denied",
        "quant.research-note.draft": "requires_approval",
    }
    assert {intent.slot_key: intent.status for intent in intents} == {
        "audit.ledger.validate": "denied",
        "quant.research-note.draft": "materialized",
    }


def test_intent_json_rejects_invalid_projections() -> None:
    good = InvocationIntent(slot_key="s", capability="audit.x", role="primary", status="denied")
    assert good.to_json()["status"] == "denied"
    with pytest.raises(ValueError, match="invalid intent role"):
        InvocationIntent(slot_key="s", capability="audit.x", role="rogue").to_json()
    with pytest.raises(ValueError, match="invalid policy decision"):
        InvocationIntent(slot_key="s", capability="audit.x", policy_decision="maybe").to_json()
    with pytest.raises(ValueError, match="invalid intent status"):
        InvocationIntent(slot_key="s", capability="audit.x", status="executing").to_json()


def test_intent_schema_forbids_executable_fields() -> None:
    validator = Draft202012Validator(json.loads((SCHEMA_DIR / "invocation-intent.schema.json").read_text(encoding="utf-8")))
    base = {
        "slot_key": "audit.ledger.validate", "role": "primary", "capability": "audit.ledger.validate",
        "expected_inputs": [], "expected_outputs": ["audit-quality-candidates"],
        "isolation": "isolated_subprocess", "side_effects": "read_only",
        "policy_decision": "allowed", "status": "policy_allowed", "trace_id": "00000000-0000-0000-0000-0000000000a1",
    }
    validator.validate(base)
    errors = list(validator.iter_errors({**base, "side_effects": "write_data"}))
    assert any("side_effects" in error.json_path for error in errors)
    errors = list(validator.iter_errors({**base, "command": "run.exe"}))
    assert errors  # additionalProperties: false rejects executable fields


# -- chain schema contracts ---------------------------------------------------


def test_invocation_chain_schema_rejects_execution_mode() -> None:
    validator = Draft202012Validator(json.loads((SCHEMA_DIR / "invocation-chain.schema.json").read_text(encoding="utf-8")))
    base = {
        "chain_key": "chain-" + "a" * 16, "plan_key": "plan-" + "b" * 16, "mode": "plan_only",
        "planner_version": "1.0.0",
        "chain_order": [
            {"slot_key": "slot.one", "blueprint_key": "bp-one", "capability": "audit.x", "ordinal": 0, "role": "primary"}
        ],
        "port_bindings": [], "created_at": "2026-09-07T00:00:00Z", "trace_id": "00000000-0000-0000-0000-0000000000a1",
    }
    validator.validate(base)
    errors = list(validator.iter_errors({**base, "mode": "execute"}))
    assert any("mode" in error.json_path for error in errors)
    # payload transmission is contractually banned
    errors = list(
        validator.iter_errors(
            {
                **base,
                "port_bindings": [
                    {"producer_slot": "slot.one", "consumer_slot": "slot.two", "relation_type": "data_flow",
                     "bind_mode": "contract_ref", "contract_ref": "x", "payload_disallowed": False}
                ],
            }
        )
    )
    assert any("payload_disallowed" in error.json_path for error in errors)


def test_chain_request_model_parses_valid_and_rejects_invalid() -> None:
    ok = ChainMaterialize.parse(
        {"plan_key": "plan-" + "c" * 16, "idempotency_key": "req-1", "reason": "M3 校验"}
    )
    assert ok.plan_key == "plan-" + "c" * 16
    assert ok.idempotency_key == "req-1"
    with pytest.raises(ValueError, match="invalid topology chain request"):
        ChainMaterialize.parse({"plan_key": "wrong-prefix", "idempotency_key": "x"})
    with pytest.raises(ValueError, match="invalid topology chain request"):
        ChainMaterialize.parse({"plan_key": "plan-" + "c" * 16})  # idempotency_key required


# -- adapter contracts --------------------------------------------------------


def test_runtime_adapter_contract_never_invokes() -> None:
    chain = _seed_chain()
    intents = IntentGenerator().intents(chain, _SEED_BLUEPRINTS)
    contract = runtime_adapter_contract(chain, intents)
    assert contract["adapter"] == "plugin_runtime"
    assert contract["mode"] == "plan_only"
    assert contract["execution_invoked"] is False
    assert [node["slot_key"] for node in contract["nodes"]] == ["audit.ledger.validate", "quant.research-note.draft"]
    assert len(contract["port_flow"]) == 1


def test_policy_adapter_contract_counts_approvals() -> None:
    chain = _seed_chain()
    intents = IntentGenerator().intents(
        chain, _SEED_BLUEPRINTS,
        decisions={"quant.research-note.draft": ("requires_approval", "materialized")},
    )
    contract = policy_adapter_contract(chain, intents)
    assert contract["mode"] == "plan_only"
    assert contract["projection"] == "read_only"
    assert contract["approval_required_total"] == 1
    by_slot = {d["slot_key"]: d for d in contract["decisions"]}
    assert by_slot["quant.research-note.draft"]["policy_decision"] == "requires_approval"


def test_agent_adapter_contract_creates_no_task() -> None:
    chain = _seed_chain()
    intents = IntentGenerator().intents(chain, _SEED_BLUEPRINTS)
    contract = agent_adapter_contract(chain, intents)
    assert contract["adapter"] == "agent_task_assembly"
    assert contract["mode"] == "plan_only"
    assert contract["no_task_created"] is True
    assert all(step["action"] == "request_approval_if_required" for step in contract["steps"])
    assert all("rollback_coordinator_ref" in step and step["rollback_coordinator_ref"] is None for step in contract["steps"])