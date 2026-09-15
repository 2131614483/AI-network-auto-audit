"""The gates a real domain needs before a model can plan it.

These pin the three things that made an AI draft of the actual plugin network
impossible — each one failed *before* the compiler was ever reached:

1. the port registry was the hand-written seven-entry ``PORT_CONTRACTS``, so
   every real port came back ``contract_mismatch``;
2. ``_MAX_DRAFT_NODES`` capped a draft at 32 nodes, and the audit network has 107;
3. the draft gate rejected ``edge_class``, so a draft could not declare the
   portless ``call`` edge that represents a declared ``invokes``.

Plus the over-pinning bug the derived registry introduced on its first cut: the
same port id can be produced as ``one`` and consumed as ``many`` (a merge input),
so ``required``/``cardinality`` follow the direction and must not be pinned.
"""
from __future__ import annotations

import pytest

from packages.ai_planner.catalog import PORT_CONTRACTS, build_port_contracts
from packages.ai_planner.composer import discover_plugins
from packages.ai_planner.domain_pack import load_pack
from packages.ai_planner.draft import validate_draft
from packages.ai_planner.workbench import domain_catalog, new_draft


def _specs(domain: str):
    return discover_plugins(globs=load_pack(domain).globs)


def _model_shaped(draft: dict) -> dict:
    """What a model returns: only the keys the draft gate allows."""
    return {
        "plan_key": "plan-probe", "nodes": draft["nodes"], "edges": draft["edges"],
        "budget": draft["budget"], "seed_inputs": draft["seed_inputs"],
    }


# -- the derived registry ---------------------------------------------------

def test_the_derived_registry_covers_the_directory() -> None:
    contracts, conflicts = build_port_contracts(_specs("audit"))
    assert len(contracts) > len(PORT_CONTRACTS) * 10, "the hand-written registry is a fraction"
    assert conflicts == {}, "contract_id -> schema_ref must not conflict"


def test_the_derived_registry_does_not_pin_direction_dependent_fields() -> None:
    """`suspicion-set` is produced `one` and consumed `many` (it is a merge
    input); pinning either would make one of the two sides invalid."""
    contracts, _ = build_port_contracts(_specs("audit"))
    assert "cardinality" not in contracts["suspicion-set"]
    assert "required" not in contracts["suspicion-set"]


# -- the three gates a full domain needs ------------------------------------

@pytest.mark.parametrize("domain", ["aiops", "audit"])
def test_a_composed_draft_passes_the_model_gate(domain: str) -> None:
    """With the derived registry, the draft's own seeds as authorized sources and
    the node cap raised, a real domain's draft is acceptable to the same gate the
    model's output goes through."""
    specs = _specs(domain)
    derived, _ = build_port_contracts(specs)
    draft = new_draft(domain, "全流程")
    result = validate_draft(
        _model_shaped(draft),
        domain_catalog(domain),
        {(str(n), str(p)) for n, p in draft["seed_inputs"]},
        port_contracts=derived,
        max_nodes=len(specs) * 2,
    )
    assert result.ok, result.issue


def test_the_hand_written_registry_still_rejects_a_foreign_port() -> None:
    """The default must not have loosened: a port outside the seven-entry
    registry is still refused when no derived registry is supplied."""
    draft = new_draft("aiops", "全流程")
    result = validate_draft(_model_shaped(draft), domain_catalog("aiops"), set())
    assert not result.ok
    assert result.issue is not None
    assert result.issue.code == "contract_mismatch"


def test_the_node_cap_is_still_enforced_by_default() -> None:
    draft = new_draft("audit", "全流程")
    result = validate_draft(_model_shaped(draft), domain_catalog("audit"), set())
    assert not result.ok
    assert result.issue is not None
    assert "exceeds" in result.issue.message


def test_a_draft_may_declare_a_call_edge() -> None:
    """The compiler accepts `edge_class`; the draft gate must not be stricter."""
    draft = new_draft("audit", "全流程")
    call_edges = [e for e in draft["edges"] if e.get("edge_class") == "call"]
    assert call_edges, "the composed audit network has declared-invokes call edges"
    specs = _specs("audit")
    derived, _ = build_port_contracts(specs)
    result = validate_draft(
        _model_shaped(draft), domain_catalog("audit"),
        {(str(n), str(p)) for n, p in draft["seed_inputs"]},
        port_contracts=derived, max_nodes=len(specs) * 2,
    )
    assert result.ok, result.issue


def test_a_seed_outside_the_authorized_sources_is_still_denied() -> None:
    """Widening the registry must not widen the data boundary."""
    specs = _specs("aiops")
    derived, _ = build_port_contracts(specs)
    draft = new_draft("aiops", "全流程")
    result = validate_draft(
        _model_shaped(draft), domain_catalog("aiops"), set(),
        port_contracts=derived, max_nodes=len(specs) * 2,
    )
    assert not result.ok
    assert result.issue is not None
    assert result.issue.code == "data_boundary_denied"


def test_the_domain_catalog_offers_exactly_the_directory() -> None:
    for domain in ("aiops", "audit"):
        specs = _specs(domain)
        catalog = domain_catalog(domain)
        assert set(catalog) == {spec.capability for spec in specs.values()}


# -- the seeder must use the same registry as the validator -----------------

def test_the_auto_seeder_uses_the_registry_it_was_given() -> None:
    """Otherwise a draft validates and is then failed by the compiler for a
    dangling input the seeder refused to bind — the port was simply not in the
    hand-written seven."""
    from packages.ai_planner.planner import _auto_seed_inputs

    specs = _specs("aiops")
    derived, _ = build_port_contracts(specs)
    draft = new_draft("aiops", "全流程")
    nodes = draft["nodes"] + [dict(draft["nodes"][1], node_instance_id="alert-triage-002")]
    authorized = {(str(n), str(p)) for n, p in draft["seed_inputs"]}

    with_default = _auto_seed_inputs(nodes, [], authorized)
    with_derived = _auto_seed_inputs(nodes, [], authorized, port_contracts=derived)
    assert ("alert-triage-002", "alert-event") not in with_default
    assert ("alert-triage-002", "alert-event") in with_derived


# -- the model must be able to see the draft it is revising -----------------

def test_the_revision_goal_carries_the_draft_itself_not_just_a_summary() -> None:
    """`build_chat_goal` folds the draft into node ids only; a revision needs the
    edges, or the model's only honest answer is a gap."""
    from packages.ai_planner.chat import build_chat_goal
    from packages.ai_planner.workbench import _draft_json_for_prompt

    draft = new_draft("aiops", "全流程")
    summary = build_chat_goal("加一个节点", base_draft=draft)
    assert "edges" not in summary, "the summary is the thing that was insufficient"

    payload = _draft_json_for_prompt(draft)
    assert '"edges"' in payload and '"seed_inputs"' in payload


def test_an_over_long_draft_is_truncated_loudly() -> None:
    draft = new_draft("aiops", "全流程")
    from packages.ai_planner.workbench import _draft_json_for_prompt

    truncated = _draft_json_for_prompt(draft, max_chars=200)
    assert len(truncated) > 200, "the marker itself is kept"
    assert "已截断" in truncated and "字符" in truncated


class _RecordingPlanner:
    """Records the goal so the test can assert what the model was shown."""

    def __init__(self) -> None:
        self.goals: list[str] = []

    def plan(self, **kwargs: object) -> object:
        from packages.ai_planner.planner import PlanningOutcome

        self.goals.append(str(kwargs.get("goal")))
        return PlanningOutcome(status="gap_report", plan_key="plan-none")


def test_revise_with_ai_shows_the_model_the_whole_draft() -> None:
    from packages.ai_planner.workbench import revise_with_ai

    draft = new_draft("aiops", "全流程")
    planner = _RecordingPlanner()
    revise_with_ai(draft, "再加一个告警接入节点", planner=planner)
    assert planner.goals, "the planner must have been asked"
    assert '"edges"' in planner.goals[0], "the model must see the edges it is revising"


def test_revise_with_ai_passes_the_draft_s_seeds_as_the_data_boundary() -> None:
    from packages.ai_planner.workbench import revise_with_ai

    draft = new_draft("aiops", "全流程")
    seen: dict[str, object] = {}

    class _Capturing(_RecordingPlanner):
        def plan(self, **kwargs: object) -> object:
            seen.update(kwargs)
            return super().plan(**kwargs)

    revise_with_ai(draft, "随便改改", planner=_Capturing())
    authorized = seen.get("authorized_sources")
    assert authorized, "an empty boundary denies every seed the draft already declares"
    assert authorized == {(str(n), str(p)) for n, p in draft["seed_inputs"]}
