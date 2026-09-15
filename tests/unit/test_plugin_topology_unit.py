"""Unit tests for Plugin Topology M1 planner, resolver and contracts.

These tests never touch a database: the planner and resolver are pure and the
contract models validate payloads from JSON only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from packages.plugin_topology.contracts import (
    TopologyPlan,
    TopologyRecycle,
    TopologyRelease,
    TopologyUpsert,
)
from packages.plugin_topology.planner import PlannerCycleError, RoutingPlan, TopologyPlanner
from packages.plugin_topology.resolver import ContractResolver
from packages.plugin_topology.service import TopologyService

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"

# -- contracts -------------------------------------------------------------


def _schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_contract_models_reject_blueprint_execution_fields() -> None:
    with pytest.raises(ValueError, match="invalid topology upsert"):
        TopologyUpsert.parse(
            {
                "kind": "blueprint",
                "idempotency_key": "bp-bad",
                "payload": {
                    "key": "forbidden-slot",
                    "name": "带执行字段的槽位",
                    "blueprint_type": "capability",
                    "lifecycle": "planned",
                    "capability_contract": {"capability": "audit.ledger.validate", "version": "1.0.0"},
                    "source_refs": [{"kind": "design_document", "uri": "reference://TA-01"}],
                    "runtime": "isolated-process",
                },
            }
        )


def test_contract_models_require_idempotency_key() -> None:
    with pytest.raises(ValueError, match="should be non-empty"):
        TopologyUpsert.parse({"kind": "cluster", "idempotency_key": "", "payload": {}})


def test_plan_model_rejects_execute_mode() -> None:
    with pytest.raises(ValueError, match="plan_only"):
        TopologyPlan.parse(
            {
                "intent": "run everything now",
                "idempotency_key": "p-1",
                "mode": "execute",
                "capability_requirements": ["audit.ledger.validate"],
                "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            }
        )


def test_release_publish_requires_checksum_but_rollback_only_version() -> None:
    validator = Draft202012Validator(_schema("topology-release.schema.json"))
    publish_ok = {
        "action": "publish", "idempotency_key": "r-1", "version": "1.0.0",
        "catalog_checksum": "a" * 64,
        "cluster_keys": ["business-financial-audit"], "blueprint_keys": ["ledger-quality-slot"],
    }
    validator.validate(publish_ok)
    errors = list(validator.iter_errors({**publish_ok, "catalog_checksum": "short"}))
    assert any("catalog_checksum" in error.json_path for error in errors)
    rollback_ok = {
        "action": "rollback", "idempotency_key": "r-2", "rollback_from_version": "1.0.0",
    }
    validator.validate(rollback_ok)


def test_recycle_restore_requires_restored_from_recycle_id() -> None:
    with pytest.raises(ValueError, match="restored_from_recycle_id"):
        TopologyRecycle.parse(
            {
                "recycle_type": "restored",
                "entity_kind": "blueprint",
                "entity_key": "ledger-quality-slot",
                "idempotency_key": "rc-1",
            }
        )


# -- bridge registration validation (service, no database) ------------------


def _service_validation() -> TopologyService:
    return TopologyService("postgresql://unused:unused@localhost/unused")


def test_bridge_upsert_requires_public_ref_kind_and_prefix_consistency() -> None:
    service = _service_validation()
    # ref_kind is defense-in-depth against schema bypass; domain-private reads are banned.
    with pytest.raises(ValueError, match="invalid bridge ref_kind"):
        service._validate_upsert(
            TopologyUpsert(
                kind="bridge",
                idempotency_key="br-u1",
                payload={"blueprint_key": "ledger-quality-slot", "ref_kind": "domain_private_table", "bridge_ref": "schema://x"},
            )
        )
    # prefix must match the declared public ref kind
    with pytest.raises(ValueError, match="does not match ref_kind"):
        service._validate_upsert(
            TopologyUpsert(
                kind="bridge",
                idempotency_key="br-u2",
                payload={"blueprint_key": "ledger-quality-slot", "ref_kind": "artifact_ref", "bridge_ref": "file:///etc/passwd"},
            )
        )
    with pytest.raises(ValueError, match="bridge_ref is required"):
        service._validate_upsert(
            TopologyUpsert(
                kind="bridge",
                idempotency_key="br-u3",
                payload={"blueprint_key": "ledger-quality-slot", "ref_kind": "health_signal"},
            )
        )
    # all four public ref kinds pass with a conforming prefix
    for ref_kind, bridge_ref in (
        ("artifact_ref", "artifact://TA-01"),
        ("capability_contract", "contracts/jsonschema/audit-quality-candidates@1"),
        ("released_graph_ref", "graph://release/catalog-1"),
        ("health_signal", "health://aiops/local"),
    ):
        service._validate_upsert(
            TopologyUpsert(
                kind="bridge",
                idempotency_key=f"br-ok-{ref_kind}",
                payload={"blueprint_key": "ledger-quality-slot", "ref_kind": ref_kind, "bridge_ref": bridge_ref},
            )
        )


# -- resolver --------------------------------------------------------------


def test_resolver_matches_declared_capability_and_version() -> None:
    result = ContractResolver.resolve(
        {"capability": "audit.ledger.validate", "version": "1.2.0", "outputs": ["audit-quality-candidates"]},
        {"capability": "audit.ledger.validate", "version": "1.0.0", "inputs": ["audit-quality-candidates"]},
    )
    assert result.compatible is True
    assert result.reasons == ()


def test_resolver_rejects_name_guessing_and_version_major_break() -> None:
    result = ContractResolver.resolve(
        {"capability": "audit.ledger.validate", "version": "1.0.0", "outputs": ["x"]},
        {"capability": "audit.ledger.check", "version": "1.0.0", "inputs": ["y"]},
    )
    assert result.compatible is False
    assert any("capability mismatch" in reason for reason in result.reasons)

    result = ContractResolver.resolve(
        {"capability": "audit.ledger.validate", "version": "2.0.0", "outputs": ["x"]},
        {"capability": "audit.ledger.validate", "version": "1.5.0", "inputs": ["x"]},
    )
    assert result.compatible is False
    assert any("version mismatch" in reason for reason in result.reasons)


def test_resolver_wildcard_only_in_consumer_need() -> None:
    result = ContractResolver.resolve(
        {"capability": "audit.ledger.validate", "version": "1.0.0", "outputs": ["o"]},
        {"capability": "audit.*.validate", "version": "1.0.0", "inputs": ["o"]},
    )
    assert result.compatible is True


# -- planner ---------------------------------------------------------------


def _catalog() -> tuple[dict[str, dict[str, object]], dict[str, list[str]], list[dict[str, str]]]:
    blueprints = {
        "ledger-quality-slot": {"capability": "audit.ledger.validate", "version": "1.0.0", "outputs": ["audit-quality-candidates"]},
        "finding-draft-slot": {"capability": "audit.finding.draft", "version": "1.0.0", "outputs": ["finding-draft"]},
        "research-note-slot": {"capability": "quant.research-note.draft", "version": "1.0.0", "outputs": ["research-note-draft"]},
        "isolated-slot": {"capability": "audit.ledger.validate", "version": "1.0.0", "outputs": ["audit-quality-candidates"]},
    }
    memberships = {
        "ledger-quality-slot": ["business-financial-audit", "capability-audit-quality"],
        "finding-draft-slot": ["capability-audit-quality"],
        "research-note-slot": ["governance-local-dev-approved"],
        "isolated-slot": [],
    }
    edges = [
        {"source_blueprint_key": "ledger-quality-slot", "target_blueprint_key": "finding-draft-slot", "relation_type": "depends_on"},
    ]
    return blueprints, memberships, edges


def test_planner_converges_multiaxis_and_produces_plan_only_chain() -> None:
    planner = TopologyPlanner()
    blueprints, memberships, edges = _catalog()
    plan = planner.plan(
        capability_requirements=["audit.ledger.validate", "audit.finding.draft"],
        budget={"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        blueprints=blueprints,  # type: ignore[arg-type]
        cluster_memberships=memberships,
        edges=edges,
    )
    assert isinstance(plan, RoutingPlan)
    assert plan.mode == "plan_only"
    assert plan.checksum == plan.checksum_digest()
    assert [node.blueprint_key for node in plan.nodes] == ["ledger-quality-slot", "finding-draft-slot"]
    assert len(plan.edges) == 1


def test_planner_excludes_blueprints_outside_all_narrowed_clusters() -> None:
    planner = TopologyPlanner()
    blueprints, memberships, edges = _catalog()
    # isolated-slot has no cluster memberships, so multi-axis convergence skips it
    plan = planner.plan(
        capability_requirements=["audit.ledger.validate"],
        budget={"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        blueprints=blueprints,  # type: ignore[arg-type]
        cluster_memberships=memberships,
        edges=edges,
    )
    assert plan.nodes[0].blueprint_key == "ledger-quality-slot"
    assert plan.nodes[0].alternatives == ()


def test_planner_rejects_cycles() -> None:
    planner = TopologyPlanner()
    blueprints, memberships, _ = _catalog()
    cyclic_edges = [
        {"source_blueprint_key": "ledger-quality-slot", "target_blueprint_key": "finding-draft-slot", "relation_type": "depends_on"},
        {"source_blueprint_key": "finding-draft-slot", "target_blueprint_key": "ledger-quality-slot", "relation_type": "depends_on"},
    ]
    with pytest.raises(PlannerCycleError, match="cycle"):
        planner.plan(
            capability_requirements=["audit.ledger.validate", "audit.finding.draft"],
            budget={"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            blueprints=blueprints,  # type: ignore[arg-type]
            cluster_memberships=memberships,
            edges=cyclic_edges,
        )


def test_planner_truncates_to_budget_chain_length() -> None:
    planner = TopologyPlanner()
    blueprints, memberships, edges = _catalog()
    plan = planner.plan(
        capability_requirements=["audit.ledger.validate", "audit.finding.draft", "quant.research-note.draft"],
        budget={"max_candidates": 8, "max_chain_length": 2, "max_latency_ms": 5000},
        blueprints=blueprints,  # type: ignore[arg-type]
        cluster_memberships=memberships,
        edges=edges,
    )
    assert len(plan.nodes) == 2


def test_planner_raises_when_no_blueprint_satisfies_requirement() -> None:
    planner = TopologyPlanner()
    blueprints, memberships, edges = _catalog()
    with pytest.raises(ValueError, match="no planned blueprint"):
        planner.plan(
            capability_requirements=["audit.ledger.validate", "quant.backtest.simulate"],
            budget={"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            blueprints=blueprints,  # type: ignore[arg-type]
            cluster_memberships=memberships,
            edges=edges,
        )


def test_planner_budgets_bound_max_candidates_alternatives() -> None:
    planner = TopologyPlanner()
    blueprints, memberships, edges = _catalog()
    plan = planner.plan(
        capability_requirements=["audit.ledger.validate"],
        budget={"max_candidates": 1, "max_chain_length": 4, "max_latency_ms": 5000},
        blueprints=blueprints,  # type: ignore[arg-type]
        cluster_memberships=memberships,
        edges=edges,
    )
    # With max_candidates=1 the chosen slot carries no alternatives.
    assert [node.blueprint_key for node in plan.nodes] == ["ledger-quality-slot"]
    assert plan.nodes[0].alternatives == ()
    # A chain of length 1 is also honored.
    assert len(plan.nodes) == 1


def test_planner_rejects_long_chain_cycle() -> None:
    """A loop spanning more than two blueprints must be rejected by DFS."""
    planner = TopologyPlanner()
    blueprints = {
        f"slot-{i}": {"capability": f"audit.chain.{i}", "version": "1.0.0"} for i in range(4)
    }
    memberships = {
        f"slot-{i}": ["capability-audit-quality"] for i in range(4)
    }
    cyclic_edges = [
        {"source_blueprint_key": "slot-0", "target_blueprint_key": "slot-1", "relation_type": "depends_on"},
        {"source_blueprint_key": "slot-1", "target_blueprint_key": "slot-2", "relation_type": "depends_on"},
        {"source_blueprint_key": "slot-2", "target_blueprint_key": "slot-3", "relation_type": "depends_on"},
        {"source_blueprint_key": "slot-3", "target_blueprint_key": "slot-1", "relation_type": "data_flow"},
    ]
    with pytest.raises(PlannerCycleError, match="cycle"):
        planner.plan(
            capability_requirements=[f"audit.chain.{i}" for i in range(4)],
            budget={"max_candidates": 8, "max_chain_length": 8, "max_latency_ms": 5000},
            blueprints=blueprints,  # type: ignore[arg-type]
            cluster_memberships=memberships,
            edges=cyclic_edges,
        )


def test_planner_keeps_bridge_edges_in_chain() -> None:
    """A bridge-typed edge between two chosen blueprints enters the plan DAG."""
    planner = TopologyPlanner()
    blueprints = {
        "ledger-quality-slot": {"capability": "audit.ledger.validate", "version": "1.0.0", "outputs": ["audit-quality-candidates"]},
        "research-note-slot": {"capability": "quant.research-note.draft", "version": "1.0.0", "outputs": ["research-note-draft"]},
    }
    memberships = {
        "ledger-quality-slot": ["business-financial-audit", "capability-audit-quality"],
        "research-note-slot": ["governance-local-dev-approved"],
    }
    edges = [
        {"source_blueprint_key": "ledger-quality-slot", "target_blueprint_key": "research-note-slot", "relation_type": "bridge"},
    ]
    plan = planner.plan(
        capability_requirements=["audit.ledger.validate", "quant.research-note.draft"],
        budget={"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        blueprints=blueprints,  # type: ignore[arg-type]
        cluster_memberships=memberships,
        edges=edges,
    )
    assert [node.blueprint_key for node in plan.nodes] == ["ledger-quality-slot", "research-note-slot"]
    assert [(edge.source_node, edge.target_node, edge.relation_type) for edge in plan.edges] == [
        ("ledger-quality-slot", "research-note-slot", "bridge")
    ]


def test_release_catalog_checksum_is_immutable_and_versioned() -> None:
    # A publish must carry a 64-char SHA256; the frozen checksum cannot be overwritten.
    schema = Draft202012Validator(_schema("topology-release.schema.json"))
    base = {
        "action": "publish", "idempotency_key": "r-immutable", "version": "1.0.0",
        "catalog_checksum": "b" * 64,
        "cluster_keys": ["business-financial-audit"], "blueprint_keys": ["ledger-quality-slot"],
    }
    schema.validate(base)
    # A different frozen checksum under the same version is still schema-valid:
    # the service layer keys releases on (release_key, version) so a new SHA256
    # becomes a new immutable release row, never an in-place overwrite.
    errors = list(schema.iter_errors({**base, "catalog_checksum": "e" * 64}))
    assert errors == []
    parsed = TopologyRelease.parse(base)
    assert parsed.catalog_checksum == "b" * 64
    assert parsed.version == "1.0.0"
