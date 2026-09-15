from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "jsonschema"


def _schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def test_multigraph_contracts_reject_unregistered_or_unbounded_routing() -> None:
    bridge = _schema("graph-bridge.schema.json")
    route = _schema("graph-route.schema.json")
    budget = _schema("graph-query-budget.schema.json")
    Draft202012Validator.check_schema(bridge)
    Draft202012Validator.check_schema(route)
    valid_bridge = {
        "source_space_key": "plugin.topology",
        "target_space_key": "audit.domain",
        "source_node_id": str(uuid4()),
        "target_node_id": str(uuid4()),
        "relation_type": "capability_contract",
        "weight": 0.8,
    }
    Draft202012Validator(bridge).validate(valid_bridge)
    invalid_bridge = {**valid_bridge, "relation_type": "arbitrary_cross_domain_link"}
    assert list(Draft202012Validator(bridge).iter_errors(invalid_bridge))

    registry = Registry().with_resources(
        [(budget["$id"], Resource.from_contents(budget))]  # type: ignore[index]
    )
    valid_route = {
        "nodes": [str(uuid4())], "edges": [], "visited_space_keys": ["plugin.topology"],
        "partial": False, "truncation_reasons": [],
        "budget": {"max_graphs": 1, "max_hops": 1, "max_frontier": 8, "max_nodes": 8,
                   "max_edges": 8, "max_bridge_hops": 0, "max_latency_ms": 2000, "min_confidence": 0},
    }
    Draft202012Validator(route, registry=registry).validate(valid_route)
    missing_budget = {key: value for key, value in valid_route.items() if key != "budget"}
    assert list(Draft202012Validator(route, registry=registry).iter_errors(missing_budget))
