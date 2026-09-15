"""Workbench edits: fail-closed, in-place, and never silently lossy.

The contract these tests pin:

* an edit either produces a draft that **compiles**, or it is refused with the
  compiler's own reasons and the draft is left byte-identical;
* a structural edit **re-seeds** — a freshly added node legitimately has unbound
  required inputs, so requiring a fully-wired graph would reject every add;
* ``set_instances`` grows a plugin **in place**, keeping the wiring the human
  already made, because "insert this reusable component one more time" is exactly
  the edit that follows a manual rewire.
"""
from __future__ import annotations

import json

import pytest

from packages.ai_planner.workbench import (
    apply_edit,
    new_draft,
    reusable_suggestions,
    validate_draft_full,
)


@pytest.fixture(scope="module")
def aiops() -> dict:
    return new_draft("aiops", "告警处置闭环")


def _snapshot(draft: dict) -> str:
    return json.dumps(draft, sort_keys=True, ensure_ascii=False)


# -- the first draft --------------------------------------------------------

def test_a_new_draft_is_valid_and_carries_its_provenance(aiops: dict) -> None:
    assert validate_draft_full(aiops).ok
    assert aiops["origin"]["producer"] == "compose_flow"
    assert aiops["origin"]["edited_by"] == ["compose"]
    assert aiops["origin"]["history"]
    assert aiops["domain"] == "aiops"


def test_a_new_draft_lists_reusable_plugins_without_wiring_them(aiops: dict) -> None:
    before = len(aiops["edges"])
    suggestions = aiops["suggestions"]["reusable_plugins"]
    assert isinstance(suggestions, list)
    # the suggestion pass must not have drawn anything
    assert len(aiops["edges"]) == before


# -- add / remove node ------------------------------------------------------

def test_adding_a_node_creates_a_second_instance_and_seeds_it(aiops: dict) -> None:
    result = apply_edit(aiops, {"kind": "add_node", "plugin_id": "aiops.alert-triage"})
    assert result.ok and result.draft is not None
    ids = [n["node_instance_id"] for n in result.draft["nodes"]
           if n["plugin_id"] == "aiops.alert-triage"]
    assert ids == ["alert-triage-001", "alert-triage-002"]
    # the new instance has unbound required inputs, so it must be seeded —
    # otherwise every add_node would be rejected by the required-input gate
    assert ["alert-triage-002", "alert-event"] in result.draft["seed_inputs"]
    assert validate_draft_full(result.draft).ok


def test_adding_an_unknown_plugin_is_refused_and_changes_nothing(aiops: dict) -> None:
    before = _snapshot(aiops)
    result = apply_edit(aiops, {"kind": "add_node", "plugin_id": "aiops.nope"})
    assert not result.ok
    assert result.draft is None
    assert _snapshot(aiops) == before


def test_removing_a_node_takes_its_edges_with_it(aiops: dict) -> None:
    result = apply_edit(aiops, {"kind": "remove_node", "node_instance_id": "alert-triage-001"})
    assert result.ok and result.draft is not None
    assert not any(n["node_instance_id"] == "alert-triage-001" for n in result.draft["nodes"])
    assert not any(
        e["source_instance"] == "alert-triage-001" or e["target_instance"] == "alert-triage-001"
        for e in result.draft["edges"]
    )
    assert validate_draft_full(result.draft).ok


def test_removing_an_unknown_node_is_refused(aiops: dict) -> None:
    before = _snapshot(aiops)
    result = apply_edit(aiops, {"kind": "remove_node", "node_instance_id": "ghost-001"})
    assert not result.ok
    assert _snapshot(aiops) == before


# -- edges ------------------------------------------------------------------

def test_an_edge_between_mismatched_contracts_is_refused(aiops: dict) -> None:
    """The compiler would reject it, so the edit must not hand it over."""
    result = apply_edit(aiops, {
        "kind": "add_edge", "source_instance": "rca-ranker-001", "source_port": "rca-candidates",
        "target_instance": "ticket-draft-001", "target_port": "incident-proposal",
    })
    assert not result.ok
    assert "contract mismatch" in result.issues[0]["message"]
    assert "adapter" in result.issues[0]["message"]


def test_an_edge_to_an_unknown_port_is_refused(aiops: dict) -> None:
    result = apply_edit(aiops, {
        "kind": "add_edge", "source_instance": "rca-ranker-001", "source_port": "no-such-port",
        "target_instance": "ticket-draft-001", "target_port": "incident-proposal",
    })
    assert not result.ok
    assert "no output port" in result.issues[0]["message"]


def test_a_second_producer_on_one_input_is_refused(aiops: dict) -> None:
    """`ticket-draft.incident-proposal` is already fed by alert-triage."""
    result = apply_edit(aiops, {
        "kind": "add_edge", "source_instance": "alert-triage-001", "source_port": "incident-proposal",
        "target_instance": "ticket-draft-001", "target_port": "incident-proposal",
    })
    assert not result.ok
    assert "already has a producer" in result.issues[0]["message"]


def test_an_edge_may_be_removed(aiops: dict) -> None:
    edge_id = aiops["edges"][0]["edge_id"]
    result = apply_edit(aiops, {"kind": "remove_edge", "edge_id": edge_id})
    assert result.ok and result.draft is not None
    assert not any(e["edge_id"] == edge_id for e in result.draft["edges"])
    assert validate_draft_full(result.draft).ok


def test_removing_an_unknown_edge_is_refused(aiops: dict) -> None:
    before = _snapshot(aiops)
    result = apply_edit(aiops, {"kind": "remove_edge", "edge_id": "e999"})
    assert not result.ok
    assert _snapshot(aiops) == before


def test_a_valid_edge_can_be_drawn_between_matching_contracts(aiops: dict) -> None:
    """`rca-ranker.rca-candidates` and `playbook-proposer` already share a
    contract; wiring a second consumer is the point of the editor."""
    stripped = apply_edit(aiops, {
        "kind": "remove_edge",
        "edge_id": next(e["edge_id"] for e in aiops["edges"]
                        if e["source_instance"] == "rca-ranker-001"),
    })
    assert stripped.ok and stripped.draft is not None
    redrawn = apply_edit(stripped.draft, {
        "kind": "add_edge", "source_instance": "rca-ranker-001", "source_port": "rca-candidates",
        "target_instance": "playbook-proposer-001", "target_port": "rca-candidates",
    })
    assert redrawn.ok, redrawn.issues
    assert validate_draft_full(redrawn.draft).ok


# -- reuse in place ---------------------------------------------------------

def test_set_instances_grows_without_losing_existing_wiring(aiops: dict) -> None:
    kept = {(e["source_instance"], e["target_instance"]) for e in aiops["edges"]}
    result = apply_edit(aiops, {"kind": "set_instances", "plugin_id": "aiops.alert-triage", "count": 3})
    assert result.ok and result.draft is not None
    ids = sorted(n["node_instance_id"] for n in result.draft["nodes"]
                 if n["plugin_id"] == "aiops.alert-triage")
    assert ids == ["alert-triage-001", "alert-triage-002", "alert-triage-003"]
    after = {(e["source_instance"], e["target_instance"]) for e in result.draft["edges"]}
    assert kept <= after, "growing instances must not drop the wiring already made"
    assert validate_draft_full(result.draft).ok


def test_set_instances_shrinks_the_highest_instances_first(aiops: dict) -> None:
    grown = apply_edit(aiops, {"kind": "set_instances", "plugin_id": "aiops.alert-triage", "count": 3})
    assert grown.ok and grown.draft is not None
    shrunk = apply_edit(grown.draft, {"kind": "set_instances", "plugin_id": "aiops.alert-triage", "count": 1})
    assert shrunk.ok and shrunk.draft is not None
    ids = [n["node_instance_id"] for n in shrunk.draft["nodes"]
           if n["plugin_id"] == "aiops.alert-triage"]
    assert ids == ["alert-triage-001"], "the settled first instance should survive"


def test_set_instances_below_one_is_refused(aiops: dict) -> None:
    result = apply_edit(aiops, {"kind": "set_instances", "plugin_id": "aiops.alert-triage", "count": 0})
    assert not result.ok
    assert ">= 1" in result.issues[0]["message"]


# -- the fail-closed rule ---------------------------------------------------

def test_an_unknown_edit_kind_is_refused(aiops: dict) -> None:
    before = _snapshot(aiops)
    result = apply_edit(aiops, {"kind": "teleport"})
    assert not result.ok
    assert "unknown edit kind" in result.issues[0]["message"]
    assert _snapshot(aiops) == before


def test_apply_edit_never_mutates_the_caller_s_draft(aiops: dict) -> None:
    before = _snapshot(aiops)
    apply_edit(aiops, {"kind": "add_node", "plugin_id": "aiops.ticket-draft"})
    apply_edit(aiops, {"kind": "remove_edge", "edge_id": aiops["edges"][0]["edge_id"]})
    assert _snapshot(aiops) == before, "a refused or accepted edit must not touch the input draft"


def test_a_successful_edit_records_its_provenance(aiops: dict) -> None:
    result = apply_edit(aiops, {"kind": "add_node", "plugin_id": "aiops.ticket-draft"})
    assert result.ok and result.draft is not None
    assert "human" in result.draft["origin"]["edited_by"]
    assert result.draft["origin"]["history"][-1]["what"] == "add_node"


# -- reuse suggestions ------------------------------------------------------

def test_suggestions_cover_the_reusable_roles_and_draw_nothing(aiops: dict) -> None:
    suggestions = reusable_suggestions(aiops, "aiops")
    for entry in suggestions:
        assert entry["role"] in {"validator", "adapter", "service"}
        assert entry["note"].startswith("仅建议")
    assert len(aiops["edges"]) == len(new_draft("aiops", "告警处置闭环")["edges"])


def test_audit_suggestions_flag_the_coarse_schemas() -> None:
    """`artifact-ref.schema.json` is a catch-all; a suggestion resting on it must
    say so, because it is not evidence that the two really connect."""
    draft = new_draft("audit", "全流程")
    suggestions = reusable_suggestions(draft, "audit")
    assert suggestions, "the audit domain has validators/adapters/services"
    coarse = [s for s in suggestions if s["coarse_schema"]]
    trusted = [s for s in suggestions if not s["coarse_schema"]]
    assert coarse, "most foundation services take the catch-all schema"
    assert trusted, "and some suggestions rest on a narrow schema"
    for entry in trusted:
        assert entry["candidates"], "a suggestion without a candidate is not a suggestion"
