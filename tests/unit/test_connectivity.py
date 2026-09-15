"""Unit tests for the connectivity / provenance harness.

Synthetic graphs, so the assertions describe the *rules* (component counting,
seed accounting, sha provenance, adapter exemption) rather than the shipped
directory — that is what ``tests/contract/test_connectivity_baseline.py`` locks.
"""
from __future__ import annotations

from packages.ai_planner.composer import PluginSpec, PortSpec
from packages.ai_planner.connectivity import (
    EDGE_SET_CONTRACT,
    EDGE_SET_CONTRACT_INVOKES,
    build_contract_index,
    connectivity_metrics,
    contract_edges,
    format_metrics_table,
    invokes_edges,
    provenance_report,
)


def _port(port_id: str, schema_ref: str = "s.json", direction: str = "input") -> PortSpec:
    return PortSpec(port_id=port_id, schema_ref=schema_ref, direction=direction)


def _plugin(plugin_id: str, *, inputs=(), outputs=(), invokes=()) -> PluginSpec:
    return PluginSpec(
        plugin_id=plugin_id,
        capability=plugin_id,
        name=plugin_id,
        description="",
        lifecycle="verified",
        domains=("audit",),
        inputs=tuple(_port(p, direction="input") for p in inputs),
        outputs=tuple(_port(p, direction="output") for p in outputs),
        invokes=tuple(invokes),
    )


def _specs() -> dict[str, PluginSpec]:
    # a → b → c is a chain; d is isolated; e declares a cross-layer call to d.
    return {
        "a": _plugin("a", outputs=("x",)),
        "b": _plugin("b", inputs=("x",), outputs=("y",)),
        "c": _plugin("c", inputs=("y",)),
        "d": _plugin("d", inputs=("external",), outputs=("z",)),
        "e": _plugin("e", inputs=("x",), invokes=("d",)),
    }


# --------------------------------------------------------------------------
# contract graph
# --------------------------------------------------------------------------

def test_contract_edges_join_on_contract_id_equality() -> None:
    edges = contract_edges(_specs())
    assert ("a", "b") in edges      # a.x → b.x
    assert ("b", "c") in edges      # b.y → c.y
    assert ("a", "e") in edges      # a.x → e.x
    assert ("d", "b") not in edges  # d.z is consumed by nobody


def test_invokes_edges_are_a_separate_relation() -> None:
    specs = _specs()
    assert invokes_edges(specs) == {("e", "d")}
    # not folded into the contract edge set: invokes is not a dataflow
    assert ("e", "d") not in contract_edges(specs)


def test_invokes_edges_skip_unknown_and_self_targets() -> None:
    specs = {
        "a": _plugin("a", invokes=("a", "ghost", "b")),
        "b": _plugin("b"),
    }
    assert invokes_edges(specs) == {("a", "b")}


def test_contract_index_reports_supply_and_demand_gaps() -> None:
    index = build_contract_index(_specs())
    assert index.dead_end_outputs == ("z",)
    assert index.unfillable_inputs == ("external",)
    assert index.reusable == ("x", "y")
    assert index.unfillable_input_ports == 1


# --------------------------------------------------------------------------
# graph shape
# --------------------------------------------------------------------------

def test_metrics_count_components_and_seeds() -> None:
    metrics = connectivity_metrics(_specs())
    assert metrics.edge_set == EDGE_SET_CONTRACT
    assert metrics.plugins == 5
    assert metrics.edges == 3
    # d is fully isolated; a/e only feed, c only consumes
    assert metrics.isolated_nodes == 1
    assert metrics.no_incoming == 2          # a, d
    assert metrics.no_outgoing == 3          # c, d, e
    assert metrics.weakly_connected_components == 2   # {a,b,c,e} + {d}
    assert metrics.component_sizes == (4, 1)
    assert metrics.unfillable_inputs == 1
    assert metrics.seeds == 1
    assert metrics.pure_seed_nodes == 2


def test_metrics_do_not_silently_switch_edge_set() -> None:
    specs = _specs()
    contract_only = connectivity_metrics(specs)
    with_invokes = connectivity_metrics(specs, include_invokes=True)
    assert contract_only.edge_set == EDGE_SET_CONTRACT
    assert with_invokes.edge_set == EDGE_SET_CONTRACT_INVOKES
    assert with_invokes.edges == contract_only.edges + 1
    # e→d reconnects the two components
    assert with_invokes.weakly_connected_components == 1
    assert with_invokes.isolated_nodes == 0


def test_metrics_accept_an_explicit_edge_set() -> None:
    metrics = connectivity_metrics(_specs(), edges=[("a", "c")])
    assert metrics.edges == 1
    assert metrics.isolated_nodes == 3


def test_explicit_edges_outside_the_plugin_set_are_ignored() -> None:
    metrics = connectivity_metrics(_specs(), edges=[("a", "c"), ("ghost", "a")])
    assert metrics.edges == 1


def test_metrics_are_json_serialisable() -> None:
    payload = connectivity_metrics(_specs()).as_dict()
    assert payload["component_sizes"] == [4, 1]
    assert payload["edge_set"] == EDGE_SET_CONTRACT


# --------------------------------------------------------------------------
# before / after table
# --------------------------------------------------------------------------

def test_metrics_table_flags_a_mixed_edge_set_comparison() -> None:
    specs = _specs()
    before = connectivity_metrics(specs)
    after = connectivity_metrics(specs, include_invokes=True)
    table = format_metrics_table(before, after)
    assert "口径不同" in table

    same = format_metrics_table(before, connectivity_metrics(specs))
    assert "口径不同" not in same


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

def _attempt(node_id: str, bindings: dict, outputs: dict) -> dict:
    return {"node_instance_id": node_id, "input_bindings": bindings, "output_refs": outputs}


def _binding(sha: str, source: str, source_port: str, adapter=None) -> dict:
    return {
        "sha256": sha, "source_instance": source, "source_port": source_port,
        "adapter": adapter, "uri": f"file:///x/{source}-{source_port}.json",
    }


def test_provenance_verifies_upstream_sha_and_counts_seeds() -> None:
    attempts = [
        _attempt("a", {"in": _binding("s" * 64, "seed", "in")}, {"out": {"sha256": "o" * 64}}),
        _attempt("b", {"in": _binding("o" * 64, "a", "out")}, {"out2": {"sha256": "p" * 64}}),
    ]
    report = provenance_report(attempts)
    assert report.ok
    assert (report.total_bindings, report.seed_bindings, report.upstream_bindings) == (2, 1, 1)
    assert report.verified == 1
    assert report.nodes_with_upstream == 1
    assert report.seed_only_nodes == 1
    assert report.upstream_share == 0.5


def test_provenance_flags_a_sha_mismatch() -> None:
    attempts = [
        _attempt("a", {}, {"out": {"sha256": "o" * 64}}),
        _attempt("b", {"in": _binding("WRONG", "a", "out")}, {}),
    ]
    report = provenance_report(attempts)
    assert not report.ok
    assert [v.reason for v in report.violations] == ["sha_mismatch"]
    assert report.verified == 0


def test_provenance_flags_a_missing_upstream_output() -> None:
    attempts = [_attempt("b", {"in": _binding("o" * 64, "ghost", "out")}, {})]
    report = provenance_report(attempts)
    assert [v.reason for v in report.violations] == ["upstream_output_missing"]


def test_provenance_reports_adapter_bindings_without_calling_them_a_mismatch() -> None:
    """An adapter re-serializes on purpose, so sha equality is not expected —
    but it must still be visible in the report, not silently passed."""
    attempts = [
        _attempt("a", {}, {"out": {"sha256": "o" * 64}}),
        _attempt("b", {"in": _binding("DIFFERENT", "a", "out", adapter="x-to-y")}, {}),
    ]
    report = provenance_report(attempts)
    assert not report.ok
    assert [v.reason for v in report.violations] == ["adapter_binding"]
    assert report.upstream_bindings == 1


def test_provenance_on_an_empty_run_is_trivially_ok() -> None:
    report = provenance_report([])
    assert report.ok
    assert report.nodes == 0
    assert report.upstream_share == 0.0


def test_provenance_tolerates_records_without_bindings() -> None:
    report = provenance_report([{"node_instance_id": "a"}, {"node_instance_id": "b", "input_bindings": None}])
    assert report.ok
    assert report.nodes == 2
    assert report.seed_only_nodes == 2
