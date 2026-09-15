"""Frozen connectivity baseline for the shipped 107-plugin contract directory.

These numbers describe the network **before** the business-semantics wiring
change.  They exist so "we improved connectivity" is a checkable claim instead
of a vibe: this file is the "before" column of the archive's comparison table.

Updating a number here is a deliberate, reviewable act — it means the shipped
contract directory's shape changed.  Do not relax an assertion to make a
refactor pass; change the number only when the directory genuinely changed, and
say why in the commit message.

Numbers verified 2026-09-12 against `plugins/builtin/audit-*` (107 plugins, 124
input ports, 103 distinct input contract ids, 107 distinct output contract ids).
"""
from __future__ import annotations

import pytest

from packages.ai_planner.composer import discover_plugins
from packages.ai_planner.connectivity import (
    connectivity_metrics,
    contract_edges,
    invokes_edges,
)

# The shipped directory is the ground truth; these are its frozen projections.
BASELINE_CONTRACT_EDGES = 69
BASELINE_ISOLATED_NODES = 35
BASELINE_NO_INCOMING = 51
BASELINE_NO_OUTGOING = 58
BASELINE_WEAK_COMPONENTS = 42
BASELINE_DEAD_END_OUTPUTS = 58
BASELINE_UNFILLABLE_INPUTS = 54
BASELINE_REUSABLE_CONTRACTS = 49
BASELINE_TOTAL_INPUT_PORTS = 124
BASELINE_UNFILLABLE_INPUT_PORTS = 55
BASELINE_PURE_SEED_NODES = 51


@pytest.fixture(scope="module")
def specs():
    return discover_plugins()


def test_baseline_plugin_and_port_counts(specs) -> None:
    assert len(specs) == 107
    assert sum(len(spec.inputs) for spec in specs.values()) == BASELINE_TOTAL_INPUT_PORTS


def test_baseline_contract_edge_set_is_locked(specs) -> None:
    metrics = connectivity_metrics(specs, edges=contract_edges(specs))
    assert metrics.edge_set == "contract"
    assert metrics.edges == BASELINE_CONTRACT_EDGES
    assert metrics.isolated_nodes == BASELINE_ISOLATED_NODES
    assert metrics.no_incoming == BASELINE_NO_INCOMING
    assert metrics.no_outgoing == BASELINE_NO_OUTGOING
    assert metrics.weakly_connected_components == BASELINE_WEAK_COMPONENTS
    assert metrics.pure_seed_nodes == BASELINE_PURE_SEED_NODES


def test_baseline_supply_demand_gap_is_locked(specs) -> None:
    """The gap is the problem statement: most outputs are dead ends and most
    unfillable inputs must be injected as seeds rather than produced by a peer."""
    metrics = connectivity_metrics(specs)
    assert metrics.dead_end_outputs == BASELINE_DEAD_END_OUTPUTS
    assert metrics.unfillable_inputs == BASELINE_UNFILLABLE_INPUTS
    assert metrics.unfillable_input_ports == BASELINE_UNFILLABLE_INPUT_PORTS
    assert metrics.seeds == BASELINE_UNFILLABLE_INPUT_PORTS
    assert metrics.reusable_contracts == BASELINE_REUSABLE_CONTRACTS


def test_component_sizes_are_locked(specs) -> None:
    sizes = connectivity_metrics(specs).component_sizes
    assert sizes[:5] == (26, 21, 13, 5, 3)
    assert sum(sizes) == len(specs)


def test_the_two_edge_sets_are_distinct_and_reported_separately(specs) -> None:
    """`invokes` is a capability call, not a dataflow.  The two definitions must
    stay separable so a connectivity number can never be inflated by silently
    switching which graph it describes."""
    contract_only = connectivity_metrics(specs, edges=contract_edges(specs))
    with_invokes = connectivity_metrics(
        specs, edges=contract_edges(specs) | invokes_edges(specs), include_invokes=True,
    )
    assert contract_only.edge_set == "contract"
    assert with_invokes.edge_set == "contract+invokes"
    # the declared cross-layer calls are a real, unused connectivity asset
    assert len(invokes_edges(specs)) == 34
    assert with_invokes.edges == contract_only.edges + 34
    assert with_invokes.isolated_nodes < contract_only.isolated_nodes
    assert with_invokes.weakly_connected_components < contract_only.weakly_connected_components
