"""Role curation: a model may propose, only the gates may decide.

The failure mode this guards against is a confidently wrong classification
silently changing how the network is wired.  So every gate is fail-closed
toward ``review``, a model outage cannot fake a role, and a rejected proposal is
kept in the artifact so the gap stays visible.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.ai_planner.composer import PluginSpec, PortSpec, discover_plugins
from packages.ai_planner.domain_pack import load_pack
from packages.ai_planner.role_curator import (
    DEFAULT_MIN_CONFIDENCE,
    RoleProposal,
    build_messages,
    curate_roles,
    parse_proposal,
    propose_role,
    write_role_map,
)
from packages.ai_planner.roles import (
    ROLE_REVIEW,
    ROLES,
    load_role_map,
    resolve_all,
)
from packages.ai_planner.semantics import load_semantics


class _FakeLLM:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[list[dict[str, str]]] = []

    def complete_json(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> Any:
        self.calls.append(messages)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _BrokenLLM:
    def complete_json(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> Any:
        raise RuntimeError("backend unreachable")


def _spec(plugin_id: str = "audit.probe.x") -> PluginSpec:
    return PluginSpec(
        plugin_id=plugin_id, capability=plugin_id, name="探针", description="测试用插件",
        lifecycle="verified", domains=("audit",),
        inputs=(PortSpec(port_id="a", schema_ref="a.schema.json", direction="input"),),
        outputs=(PortSpec(port_id="b", schema_ref="b.schema.json", direction="output"),),
    )


# -- the gates --------------------------------------------------------------

def test_a_well_formed_proposal_is_accepted() -> None:
    proposal = parse_proposal("p", {"role": "sink", "confidence": 0.9, "reason": "终端产物"})
    assert proposal.accepted
    assert proposal.role == "sink"
    assert proposal.rejection == ""


def test_an_unknown_role_is_rejected() -> None:
    proposal = parse_proposal("p", {"role": "wizard", "confidence": 0.99, "reason": "乱编"})
    assert not proposal.accepted
    assert proposal.role == ROLE_REVIEW
    assert "未知角色" in proposal.rejection


def test_a_missing_reason_is_rejected() -> None:
    proposal = parse_proposal("p", {"role": "sink", "confidence": 0.9, "reason": "  "})
    assert not proposal.accepted
    assert "缺少判定依据" in proposal.rejection


def test_low_confidence_is_rejected() -> None:
    proposal = parse_proposal("p", {"role": "sink", "confidence": 0.4, "reason": "猜的"})
    assert not proposal.accepted
    assert "低于阈值" in proposal.rejection


def test_a_model_saying_it_cannot_tell_is_rejected() -> None:
    """'I don't know' must leave the plugin visibly unclassified, not guessed."""
    proposal = parse_proposal("p", {"role": ROLE_REVIEW, "confidence": 0.1, "reason": "信息不足"})
    assert not proposal.accepted
    assert "无法判断" in proposal.rejection


def test_a_non_object_answer_is_rejected() -> None:
    for raw in ("sink", None, 42, ["sink"]):
        assert not parse_proposal("p", raw).accepted


def test_a_non_numeric_confidence_is_rejected() -> None:
    proposal = parse_proposal("p", {"role": "sink", "confidence": "high", "reason": "x"})
    assert not proposal.accepted
    assert "不是数字" in proposal.rejection


def test_facets_are_filtered_to_the_vocabulary() -> None:
    proposal = parse_proposal("p", {
        "role": "service", "confidence": 0.9, "reason": "被调用",
        "facets": ["ingress", "wizard", ROLE_REVIEW, "scheduler"],
    })
    assert proposal.accepted
    assert proposal.facets == ("ingress", "scheduler")


def test_the_threshold_is_the_documented_default() -> None:
    assert DEFAULT_MIN_CONFIDENCE == 0.6
    assert parse_proposal("p", {"role": "sink", "confidence": DEFAULT_MIN_CONFIDENCE, "reason": "x"}).accepted


# -- the model call ---------------------------------------------------------

def test_a_model_outage_never_fakes_a_role() -> None:
    proposal = propose_role("p", _spec(), _BrokenLLM())
    assert not proposal.accepted
    assert proposal.role == ROLE_REVIEW
    assert "模型不可用" in proposal.rejection


def test_the_prompt_states_the_closed_vocabulary_and_the_plugin_facts() -> None:
    messages = build_messages("audit.probe.x", _spec())
    user = messages[-1]["content"]
    assert "audit.probe.x" in user
    for role in ROLES:
        if role != ROLE_REVIEW:
            assert role in user
    assert "不得" in messages[0]["content"]


def test_curate_roles_only_asks_about_unclassified_plugins() -> None:
    specs = discover_plugins()
    catalog = load_semantics()
    # curated against a clean slate, so the expectation does not depend on how
    # much of the directory the shipped role map already covers
    assignments = resolve_all(specs, catalog=catalog, pack_roles={})
    expected = sorted(p for p in specs if assignments[p].role == ROLE_REVIEW)
    llm = _FakeLLM({"role": "sink", "confidence": 0.9, "reason": "x"})
    proposals = curate_roles(
        specs, load_pack(), llm, catalog=catalog, pack_roles={}, limit=5,
    )
    assert len(llm.calls) == 5
    assert len(proposals) == 5
    assert [p.plugin_id for p in proposals] == expected[:5]


def test_curate_roles_can_target_every_plugin() -> None:
    specs = discover_plugins()
    llm = _FakeLLM({"role": "sink", "confidence": 0.9, "reason": "x"})
    proposals = curate_roles(specs, load_pack(), llm, only_review=False, limit=3)
    assert len(proposals) == 3


# -- the artifact -----------------------------------------------------------

def test_write_role_map_keeps_rejected_proposals_for_audit(tmp_path: Path) -> None:
    proposals = (
        RoleProposal("p.a", "sink", 0.9, "终端产物", accepted=True),
        RoleProposal("p.b", ROLE_REVIEW, 0.2, "信息不足", rejection="模型自述无法判断"),
    )
    payload = write_role_map(tmp_path / "role-map.json", proposals)
    assert set(payload["roles"]) == {"p.a"}
    assert set(payload["rejected"]) == {"p.b"}
    assert payload["rejected"]["p.b"]["rejection"]


def test_write_then_load_round_trips_only_accepted_entries(tmp_path: Path) -> None:
    path = tmp_path / "role-map.json"
    write_role_map(path, (
        RoleProposal("p.a", "service", 0.95, "被调用", accepted=True),
        RoleProposal("p.b", ROLE_REVIEW, 0.1, "不知道", rejection="模型自述无法判断"),
    ))
    assert load_role_map(path) == {"p.a": "service"}


def test_a_missing_role_map_is_the_previous_behaviour(tmp_path: Path) -> None:
    assert load_role_map(tmp_path / "absent.json") == {}


def test_load_ignores_rejected_and_out_of_vocabulary_entries(tmp_path: Path) -> None:
    path = tmp_path / "role-map.json"
    path.write_text(json.dumps({"roles": {
        "p.a": {"role": "service", "accepted": True},
        "p.b": {"role": "service", "accepted": False},
        "p.c": {"role": "wizard", "accepted": True},
        "p.d": {"role": ROLE_REVIEW, "accepted": True},
    }}), encoding="utf-8")
    assert load_role_map(path) == {"p.a": "service"}


def test_a_corrupt_role_map_degrades_to_no_override(tmp_path: Path) -> None:
    path = tmp_path / "role-map.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_role_map(path) == {}


# -- it actually changes the classification ---------------------------------

def test_an_accepted_role_overrides_the_heuristic(tmp_path: Path) -> None:
    """The point of the artifact: a curated role takes effect everywhere."""
    plugin_id = "audit.report-draft"
    specs = discover_plugins()
    catalog = load_semantics()
    before = resolve_all(specs, catalog=catalog, pack_roles={})[plugin_id]
    assert before.role == ROLE_REVIEW, "the heuristics are expected to leave this one undecided"

    path = tmp_path / "role-map.json"
    write_role_map(path, (RoleProposal(plugin_id, "sink", 0.9, "报告终端产物", accepted=True),))
    curated = load_role_map(path)
    after = resolve_all(specs, catalog=catalog, pack_roles=curated)[plugin_id]
    assert after.role == "sink"
    assert after.source == "pack"


def test_a_rejected_role_never_changes_the_classification(tmp_path: Path) -> None:
    plugin_id = "audit.report-draft"
    specs = discover_plugins()
    path = tmp_path / "role-map.json"
    write_role_map(path, (RoleProposal(plugin_id, "sink", 0.2, "?", rejection="置信度过低"),))
    curated = load_role_map(path)
    assert resolve_all(specs, catalog=None, pack_roles=curated)[plugin_id].role == ROLE_REVIEW
    assert curated == {}


def test_the_curator_never_produces_a_role_outside_the_vocabulary() -> None:
    specs = discover_plugins()
    llm = _FakeLLM({"role": "wizard", "confidence": 0.99, "reason": "x"})
    for proposal in curate_roles(specs, load_pack(), llm, limit=4):
        assert proposal.role in ROLES
        assert not proposal.accepted


def test_role_map_shape_is_stable(tmp_path: Path) -> None:
    payload = write_role_map(tmp_path / "role-map.json", (), domain="aiops")
    assert payload["schema_version"]
    assert payload["domain"] == "aiops"
    assert "reviewed_by" in payload
    assert isinstance(payload["roles"], dict)
