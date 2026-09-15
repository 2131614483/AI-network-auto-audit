"""Edge classes: `data` (the original) plus portless `call`/`control`/`bridge`.

The point of these tests is the *distinction*: a `call` edge represents a
declared capability invocation (``capabilities[].invokes`` — a reusable service
called from many places).  It orders execution and makes the service a real
member of the graph, but it carries no artifact, so it must never be treated as
satisfying an input.  Conflating the two would either make every valid plan
fail to compile or let a required input go unbound.
"""
from __future__ import annotations

import pytest

from packages.ai_planner.composer import compile_flow, compose_flow, discover_plugins
from packages.plugin_topology.compiler import CompileError, compile_plan, topological_order
from packages.plugin_topology.ir import EDGE_CLASS_DATA, IREdge


def _port(port_id: str, direction: str, schema_ref: str = "s.json", *, required: bool = True) -> dict:
    return {
        "port_id": port_id,
        "direction": direction,
        "schema_ref": schema_ref,
        "schema_version": "1.0.0",
        "schema_sha256": "a" * 64,
        "media_type": "application/json",
        "required": required,
        "cardinality": "one",
        "classification": "internal",
        "transport": "artifact_ref",
    }


def _node(node_id: str, *, inputs=(), outputs=()) -> dict:
    return {
        "node_instance_id": node_id,
        "plugin_id": f"p.{node_id}",
        "capability": f"p.{node_id}",
        "input_ports": [_port(p, "input") for p in inputs],
        "output_ports": [_port(p, "output") for p in outputs],
    }


def _draft(nodes, edges, seeds=()) -> dict:
    return {
        "plan_key": "plan-edge-class",
        "nodes": nodes,
        "edges": edges,
        "budget": {"max_chain_length": 128, "max_candidates": 5000, "max_latency_ms": 60000},
        # the compiler wants hashable (node, port) pairs
        "seed_inputs": [tuple(s) for s in seeds],
    }


def _call(edge_id: str, source: str, target: str) -> dict:
    return {
        "edge_id": edge_id,
        "source_instance": source,
        "source_port": "",
        "target_instance": target,
        "target_port": "",
        "edge_class": "call",
    }


# -- compilation -----------------------------------------------------------

def test_call_edge_compiles_and_orders_execution() -> None:
    """The callee (the reusable service) must run before its caller."""
    plan = compile_plan(**_draft(
        [_node("caller", outputs=("out",)), _node("svc", inputs=("in",))],
        [_call("c001", "svc", "caller")],
        seeds=[("svc", "in")],
    ))
    assert len(plan.edges) == 1
    assert plan.edges[0].edge_class == "call"
    assert not plan.edges[0].is_data
    order = topological_order(plan.nodes, plan.edges)
    assert order.index("svc") < order.index("caller")


def test_call_edge_does_not_satisfy_a_required_input() -> None:
    """A portless edge carries no artifact — treating it as a binding would
    let a node run with an unbound required input."""
    with pytest.raises(CompileError, match="required input"):
        compile_plan(**_draft(
            [_node("caller", outputs=("out",)), _node("svc", inputs=("in",))],
            [_call("c001", "caller", "svc")],
        ))


def test_node_level_fan_in_via_call_edges_is_legal() -> None:
    """A service called from N places is one node with N call edges — not a
    port fan-in violation (that gate is about ports)."""
    plan = compile_plan(**_draft(
        [_node("a", outputs=("oa",)), _node("b"), _node("svc", inputs=("in",))],
        [_call("c001", "svc", "a"), _call("c002", "svc", "b")],
        seeds=[("svc", "in")],
    ))
    assert len(plan.edges) == 2


def test_call_edge_must_not_declare_ports_or_an_adapter() -> None:
    bad = _call("c001", "caller", "svc")
    bad["target_port"] = "in"
    with pytest.raises(CompileError, match="must not declare ports"):
        compile_plan(**_draft(
            [_node("caller", outputs=("out",)), _node("svc", inputs=("in",))],
            [bad],
            seeds=[("svc", "in")],
        ))

    with_adapter = _call("c002", "caller", "svc")
    with_adapter["adapter"] = "x"
    with pytest.raises(CompileError, match="must not declare an adapter"):
        compile_plan(**_draft(
            [_node("caller", outputs=("out",)), _node("svc", inputs=("in",))],
            [with_adapter],
            seeds=[("svc", "in")],
        ))


def test_unknown_edge_class_is_rejected() -> None:
    bad = _call("c001", "caller", "svc")
    bad["edge_class"] = "telepathy"
    with pytest.raises(CompileError, match="unknown edge_class"):
        compile_plan(**_draft(
            [_node("caller", outputs=("out",)), _node("svc", inputs=("in",))],
            [bad],
            seeds=[("svc", "in")],
        ))


def test_control_edge_is_accepted_as_a_scheduling_dependency() -> None:
    edge = _call("k001", "caller", "svc")
    edge["edge_class"] = "control"
    plan = compile_plan(**_draft(
        [_node("caller", outputs=("out",)), _node("svc", inputs=("in",))],
        [edge],
        seeds=[("svc", "in")],
    ))
    assert plan.edges[0].edge_class == "control"


# -- hash backward compatibility -------------------------------------------

def test_data_edges_hash_exactly_as_before_edge_classes_existed() -> None:
    """The whole point of the default: a plan made of data edges must keep the
    identity it had before ``edge_class`` was introduced, or every stored plan
    hash changes."""
    edge = IREdge(
        edge_id="e001", source_instance="a", source_port="p",
        target_instance="b", target_port="p", adapter=None,
    )
    assert edge.edge_class == EDGE_CLASS_DATA
    assert "edge_class" not in edge._hash_payload()


def test_non_data_edges_do_enter_the_hash() -> None:
    payload = IREdge(
        edge_id="c001", source_instance="a", source_port="",
        target_instance="b", target_port="", edge_class="call",
    )._hash_payload()
    assert payload["edge_class"] == "call"


# -- composer --------------------------------------------------------------

def test_composer_turns_declared_invokes_into_call_edges() -> None:
    specs = discover_plugins()
    flow = compose_flow(goal="全流程", select=sorted(specs), chain=None, plan_key="net")
    report = flow["wiring_report"]
    call_edges = [e for e in flow["edges"] if e.get("edge_class") == "call"]
    assert report["call_edges"] == len(call_edges) > 0
    assert len(flow["edges"]) == report["data_edges"] + report["call_edges"]

    # every call edge must correspond to a declared `invokes`.  Direction is
    # service -> caller ("the caller depends on the capability it invokes"),
    # matching the reading the data edges have.
    for edge in call_edges:
        service = _plugin_of(flow, edge["source_instance"])
        caller = _plugin_of(flow, edge["target_instance"])
        assert service in specs[caller].invokes, (service, caller)
        assert edge["source_port"] == "" and edge["target_port"] == ""


def test_composer_reports_the_call_edges_it_dropped_for_cycles() -> None:
    """Dropping must be visible: a silently discarded edge reads as 'wired'
    when it is not."""
    specs = discover_plugins()
    flow = compose_flow(goal="全流程", select=sorted(specs), chain=None, plan_key="net")
    for dropped in flow["wiring_report"]["dropped_call_edges"]:
        assert dropped["reason"] == "dropped_cycle"
        assert dropped["source_instance"] and dropped["target_instance"]


def test_composed_network_with_call_edges_compiles() -> None:
    specs = discover_plugins()
    flow = compose_flow(goal="全流程", select=sorted(specs), chain=None, plan_key="net")
    plan = compile_flow(flow)
    assert len(plan.nodes) == len(flow["nodes"])
    assert len(plan.edges) == len(flow["edges"])
    assert any(not e.is_data for e in plan.edges)


def _plugin_of(flow: dict, node_instance_id: str) -> str:
    for node in flow["nodes"]:
        if node["node_instance_id"] == node_instance_id:
            return str(node["plugin_id"])
    raise KeyError(node_instance_id)
