"""Composition agents: a pipeline that can explain itself.

Two properties matter here, and both are about restraint rather than capability:

* an agent produces a **draft section**, never edges or a plan — injecting a
  model cannot widen what the pipeline is allowed to emit;
* every step has a deterministic path, so the pipeline is exercisable without a
  model being reachable at all.
"""
from __future__ import annotations

from typing import Any, Mapping

import pytest

from packages.ai_planner.composer import ComposeError, discover_plugins
from packages.ai_planner.composition import (
    AGENT_CAPABILITY_SCOUT,
    AGENT_CHAIN_WEAVER,
    AGENT_CONTRACT_CURATOR,
    AGENT_DOMAIN_PLANNER,
    AGENT_ISLAND_AUDITOR,
    AGENT_NAMES_ZH,
    AGENT_WIRING_CRITIC,
    audit_islands,
    compose_with_trace,
)
from packages.ai_planner.domain_pack import load_pack
from packages.ai_planner.semantics import load_semantics

EXPECTED_ORDER = (
    AGENT_DOMAIN_PLANNER,
    AGENT_CAPABILITY_SCOUT,
    AGENT_CHAIN_WEAVER,
    AGENT_WIRING_CRITIC,
    AGENT_ISLAND_AUDITOR,
    AGENT_CONTRACT_CURATOR,
)


def _run(domain: str = "audit", **kwargs: Any):
    pack = load_pack(domain)
    kwargs.setdefault("select", sorted(discover_plugins(globs=pack.globs)))
    kwargs.setdefault("plan_key", "p")
    return compose_with_trace(goal="全流程", domain=domain, **kwargs)


# -- the pipeline -----------------------------------------------------------

def test_the_pipeline_runs_every_agent_in_order() -> None:
    composition = _run()
    assert tuple(s.agent for s in composition.trace.steps) == EXPECTED_ORDER
    assert all(s.agent in AGENT_NAMES_ZH for s in composition.trace.steps)
    assert all(s.detail for s in composition.trace.steps), "every step must report something"


def test_nothing_uses_a_model_by_default() -> None:
    """CI must never depend on a model being reachable."""
    assert all(step.used_model is False for step in _run().trace.steps)


def test_the_pipeline_never_compiles() -> None:
    """The flow is still raw JSON — `compile_plan` stays the only authority."""
    flow = _run().flow
    assert isinstance(flow, dict)
    assert "nodes" in flow and "edges" in flow
    assert not hasattr(flow, "execution_hash")


def test_the_same_pipeline_runs_for_another_domain() -> None:
    composition = _run("aiops")
    assert tuple(s.agent for s in composition.trace.steps) == EXPECTED_ORDER
    assert composition.trace.domain == "aiops"
    assert len(composition.flow["nodes"]) == 7


def test_trace_is_json_serialisable_and_names_its_agents_in_chinese() -> None:
    payload = _run().trace.as_dict()
    assert payload["domain"] == "audit"
    assert [s["agent"] for s in payload["steps"]] == list(EXPECTED_ORDER)
    assert payload["steps"][0]["agent_zh"] == "领域规划"


# -- individual agents ------------------------------------------------------

def test_domain_planner_never_invents_a_stage_outside_the_pack() -> None:
    pack = load_pack("aiops")
    composition = _run("aiops")
    skeleton = composition.trace.step(AGENT_DOMAIN_PLANNER)
    assert skeleton is not None
    allowed = set(pack.stage_order)
    for entry in skeleton.detail["stages"]:
        assert entry["stage"] in allowed


def test_capability_scout_role_counts_cover_the_directory() -> None:
    composition = _run()
    scout = composition.trace.step(AGENT_CAPABILITY_SCOUT)
    assert scout is not None
    assert sum(scout.detail["by_role"].values()) == scout.detail["count"] == 107


def test_weaver_reports_the_flows_own_numbers() -> None:
    composition = _run()
    weaver = composition.trace.step(AGENT_CHAIN_WEAVER)
    report = composition.flow["wiring_report"]
    assert weaver is not None
    assert weaver.detail["data_edges"] == report["data_edges"]
    assert weaver.detail["call_edges"] == report["call_edges"]
    assert weaver.detail["seeds"] == report["seeds"]


def test_critic_is_silent_when_no_edge_is_schema_only() -> None:
    critic = _run().trace.step(AGENT_WIRING_CRITIC)
    assert critic is not None
    assert critic.detail["untrusted_schema_only_edges"] == 0
    assert critic.issues == ()


def test_critic_objects_to_schema_only_edges() -> None:
    """The veto is the reason the fallback stays off: those edges are not
    evidence, and a reviewer must not mistake them for it."""
    critic = _run(allow_schema_fallback=True).trace.step(AGENT_WIRING_CRITIC)
    assert critic is not None
    assert critic.detail["untrusted_schema_only_edges"] > 0
    assert [i.code for i in critic.issues] == ["unsupported_feature"]


def test_island_auditor_accounts_for_every_island() -> None:
    """Every island lands in exactly one category — none dropped from the tally.

    Whether the `unreviewed` bucket is non-empty depends on how much of the
    directory has been curated, so that is asserted against the heuristics
    (`pack_roles={}`) rather than the shipped role map.
    """
    auditor = _run().trace.step(AGENT_ISLAND_AUDITOR)
    assert auditor is not None
    assert auditor.detail["islands"] == sum(auditor.detail["by_category"].values())

    uncurated = audit_islands(
        discover_plugins(), _run().flow, load_semantics(), pack_roles={},
    )
    assert uncurated.detail["unreviewed"], "unreviewed islands must stay visible"


def test_island_auditor_raises_an_issue_only_for_real_gaps() -> None:
    """`missing_upstream` is a hole; a by-design external input is not."""
    auditor = _run().trace.step(AGENT_ISLAND_AUDITOR)
    assert auditor is not None
    assert auditor.detail["by_category"].get("missing_upstream", 0) == 0
    assert auditor.issues == ()


def test_contract_curator_exposes_suggestions_that_are_not_active() -> None:
    curator = _run().trace.step(AGENT_CONTRACT_CURATOR)
    catalog = load_semantics()
    assert curator is not None
    assert curator.detail["confirmed_groups"] == len(catalog.alias_groups)
    assert curator.detail["suggested_groups"] == len(catalog.suggested_alias_groups)
    # A suggestion must not have become an alias: canonicalising its member still
    # returns the member itself, so it creates no edges.
    for suggestion in curator.detail["suggestions"]:
        for member in suggestion["members"]:
            assert catalog.canonical(member) != suggestion["canonical"], member


# -- model injection --------------------------------------------------------

class _EchoAgent:
    """A fake model-backed agent: it only ever returns a detail payload."""

    def __init__(self, name: str, detail: Mapping[str, Any]) -> None:
        self.name = name
        self._detail = detail
        self.seen: dict[str, Any] = {}

    def run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        self.seen = dict(context)
        return {"detail": dict(self._detail)}


def test_an_injected_agent_replaces_the_step_and_says_it_used_a_model() -> None:
    agent = _EchoAgent(AGENT_DOMAIN_PLANNER, {"stages": [], "note": "from a model"})
    composition = _run(agents={AGENT_DOMAIN_PLANNER: agent})
    step = composition.trace.step(AGENT_DOMAIN_PLANNER)
    assert step is not None
    assert step.used_model is True
    assert step.detail == {"stages": [], "note": "from a model"}
    assert agent.seen["goal"] == "全流程"


def test_an_injected_agent_cannot_change_the_wiring() -> None:
    """It returns a draft section, not edges — so the flow is byte-identical."""
    baseline = _run().flow
    meddled = _run(agents={
        AGENT_DOMAIN_PLANNER: _EchoAgent(AGENT_DOMAIN_PLANNER, {"stages": ["invented"]}),
        AGENT_CHAIN_WEAVER: _EchoAgent(AGENT_CHAIN_WEAVER, {"data_edges": 9999}),
    }).flow
    assert {e["edge_id"] for e in baseline["edges"]} == {e["edge_id"] for e in meddled["edges"]}


def test_an_injected_agent_is_only_consulted_for_its_own_step() -> None:
    composition = _run(agents={AGENT_WIRING_CRITIC: _EchoAgent(AGENT_WIRING_CRITIC, {"x": 1})})
    used = [s.agent for s in composition.trace.steps if s.used_model]
    assert used == [AGENT_WIRING_CRITIC]


def test_a_goal_that_matches_nothing_fails_closed() -> None:
    with pytest.raises(ComposeError, match="no plugin matched"):
        compose_with_trace(goal="zzzzz", select=None, domain="audit")
