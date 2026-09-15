"""Island classification: explain, never hide.

The point of these tests is honesty, not connectivity.  A plugin whose input is
an external file, a permission request or a caller context has no place on a
data chain; force-wiring it invents a dependency.  So the guarantees are:

* every island gets a category — none is dropped from the tally;
* an unclassified role is reported as ``unreviewed`` and counted, never folded
  into a benign bucket to make the number look better;
* a node whose role *does* expect an upstream but has none is reported as
  ``missing_upstream`` (a real gap), not as a terminal product.
"""
from __future__ import annotations

from packages.ai_planner.composer import PluginSpec, PortSpec, compose_flow, discover_plugins
from packages.ai_planner.connectivity import (
    ISLAND_CATEGORIES,
    ISLAND_EXTERNAL_INPUT,
    ISLAND_MISSING_UPSTREAM,
    ISLAND_SUPPORT_UTILITY,
    ISLAND_TERMINAL_PRODUCT,
    ISLAND_UNREVIEWED,
    classify_islands,
    contract_edges,
    flow_edges_by_plugin,
    invokes_edges,
    island_summary,
)
from packages.ai_planner.semantics import load_semantics


def _plugin(plugin_id: str, *, inputs=(), outputs=(), invokes=()) -> PluginSpec:
    def ports(names, direction):
        return tuple(PortSpec(port_id=n, schema_ref=f"{n}.schema.json", direction=direction) for n in names)
    return PluginSpec(
        plugin_id=plugin_id, capability=plugin_id, name=plugin_id, description="",
        lifecycle="verified", domains=("audit",),
        inputs=ports(inputs, "input"), outputs=ports(outputs, "output"),
        invokes=tuple(invokes),
    )


def _category(islands, plugin_id: str) -> str:
    return next(i.category for i in islands if i.plugin_id == plugin_id)


# -- the mapping helper (a measurement trap) --------------------------------

def test_flow_edges_are_translated_from_instances_to_plugins() -> None:
    """Without this translation every plugin looks isolated, which reads exactly
    like a total connectivity collapse."""
    flow = compose_flow(goal="全流程", select=sorted(discover_plugins()), plan_key="p")
    pairs = flow_edges_by_plugin(flow)
    assert pairs, "the composed flow has edges"
    assert all("." in source and "." in target for source, target in pairs), (
        "edges must be in plugin-id space, not node_instance_id space"
    )
    assert ("audit.foundation.finance-clean", "audit.risk.finance-anomaly-alert") in pairs


def test_identity_edges_are_not_reported_as_connections() -> None:
    flow = {
        "nodes": [{"node_instance_id": "a-001", "plugin_id": "p.a"}],
        "edges": [{"source_instance": "a-001", "target_instance": "a-001"}],
    }
    assert flow_edges_by_plugin(flow) == set()


# -- categories -------------------------------------------------------------

def test_every_island_gets_a_category() -> None:
    specs = discover_plugins()
    islands = classify_islands(specs, edges=contract_edges(specs), catalog=load_semantics())
    isolated = {p for p in specs} - {p for pair in contract_edges(specs) for p in pair}
    assert {i.plugin_id for i in islands} == isolated
    assert all(i.category in ISLAND_CATEGORIES for i in islands)
    assert all(i.reason for i in islands), "every island must carry a stated reason"


def test_a_called_service_is_a_support_utility_not_a_gap() -> None:
    """`metric-compute` is invoked by four plugins; it is not missing an input."""
    specs = {
        "a": _plugin("a", outputs=("x",), invokes=("svc",)),
        "svc": _plugin("svc", inputs=("req",), outputs=("out",)),
    }
    islands = classify_islands(specs, edges=set(), pack_roles={"svc": "service"})
    assert _category(islands, "svc") == ISLAND_SUPPORT_UTILITY


def test_an_ingress_plugin_is_external_input_by_design() -> None:
    specs = {"ing": _plugin("ing", inputs=("ocr-image",), outputs=("ocr-text",))}
    islands = classify_islands(specs, edges=set(), pack_roles={"ing": "ingress"})
    assert _category(islands, "ing") == ISLAND_EXTERNAL_INPUT


def test_a_transform_with_an_unproducible_input_is_a_real_gap() -> None:
    """The role expects an upstream.  Calling it a terminal product would hide a
    genuine hole in the network."""
    specs = {"tr": _plugin("tr", inputs=("beta",), outputs=("gamma",))}
    islands = classify_islands(specs, edges=set(), pack_roles={"tr": "transform"})
    assert _category(islands, "tr") == ISLAND_MISSING_UPSTREAM


def test_a_transform_whose_inputs_are_all_producible_ends_the_chain() -> None:
    specs = {
        "src": _plugin("src", outputs=("beta",)),
        "tr": _plugin("tr", inputs=("beta",), outputs=("gamma",)),
    }
    islands = classify_islands(
        specs, edges={("src", "src")}, pack_roles={"tr": "transform"},
    )
    assert _category(islands, "tr") == ISLAND_TERMINAL_PRODUCT


def test_an_unreviewed_role_is_counted_not_buried() -> None:
    """A node whose role nobody has judged must stay visible in the tally.

    Pinned against the *heuristics* (``pack_roles={}``) rather than the shipped
    role map: the rule is "unreviewed is reported, never zeroed", and asserting
    it against whatever the map currently says would break on every curation.
    """
    specs = discover_plugins()
    islands = classify_islands(
        specs, edges=contract_edges(specs), catalog=load_semantics(), pack_roles={},
    )
    summary = island_summary(islands)
    assert summary[ISLAND_UNREVIEWED] > 0, "unreviewed islands must be reported, never zeroed"
    assert sum(summary.values()) == len(islands)


def test_summary_covers_every_category_key() -> None:
    summary = island_summary(())
    assert set(summary) == set(ISLAND_CATEGORIES)
    assert all(count == 0 for count in summary.values())


# -- the shipped directory --------------------------------------------------

def test_invokes_reduce_islands_without_inventing_data_edges() -> None:
    """The 34 `invokes` declarations reconnect 20 plugins.  That is a structural
    gain, not a data claim: the same run reports zero `missing_upstream`."""
    specs = discover_plugins()
    catalog = load_semantics()
    before = classify_islands(specs, edges=contract_edges(specs), catalog=catalog, pack_roles={})
    after = classify_islands(
        specs, edges=contract_edges(specs) | invokes_edges(specs), catalog=catalog, pack_roles={},
    )
    assert len(after) < len(before)
    assert island_summary(after)[ISLAND_MISSING_UPSTREAM] == 0
    assert island_summary(after)[ISLAND_TERMINAL_PRODUCT] == 0


def test_the_composed_flow_leaves_no_unexplained_island() -> None:
    """The end state: every remaining island is either a declared external input
    or explicitly awaiting a role review — nothing is unexplained."""
    specs = discover_plugins()
    flow = compose_flow(goal="全流程", select=sorted(specs), plan_key="p")
    flow_edges = flow_edges_by_plugin(flow)
    islands = classify_islands(specs, edges=flow_edges, catalog=load_semantics())
    summary = island_summary(islands)
    present = {category for category, count in summary.items() if count}
    # The delivered state: every island has a role-based explanation.  No gaps,
    # and nothing left unexplained — which is the acceptance criterion, so it is
    # asserted against the shipped artifacts rather than the heuristics.
    assert ISLAND_MISSING_UPSTREAM not in present, "a real gap must have been declared or fixed"
    assert ISLAND_UNREVIEWED not in present, "every island must have a decided role"
    assert present <= {
        ISLAND_EXTERNAL_INPUT, ISLAND_SUPPORT_UTILITY, ISLAND_TERMINAL_PRODUCT,
    }
    assert len(islands) == len(specs) - len({p for pair in flow_edges for p in pair})
