"""Domain packs: the framework must not be audit-only.

Two claims are tested here.  First, that loading the audit pack changes
**nothing** — it reproduces the values that used to be hardcoded in
``nebula_graph`` verbatim, so the refactor is behaviour-preserving.  Second, that
a second pack (aiops) makes a different domain genuinely composable and
compilable with the *same* composer and compiler, which is what "the framework
generalises" has to mean if it is to mean anything.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from packages.ai_planner import nebula_graph
from packages.ai_planner.composer import compile_flow, compose_flow, discover_plugins
from packages.ai_planner.domain_pack import (
    LAYER_BASE,
    LAYER_BIZ,
    LAYER_GOV,
    DomainPack,
    Stage,
    available_domains,
    load_pack,
    validate_pack,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "builtin"


# -- the audit pack reproduces the old hardcoded values ---------------------

def test_audit_pack_reproduces_the_previous_stage_table() -> None:
    """Byte-for-byte: this is what makes the refactor behaviour-preserving."""
    expected = (
        ("s1", "立项与准备", "#6f9bff", ("mandate",)),
        ("s2", "风险识别与评估", "#5fb8ff", ("risk",)),
        ("s3", "计划与资源调度", "#4fd8e0", ("plan", "investigation")),
        ("s4", "现场审计实施", "#52e0c4", ("field", "ledger", "journal")),
        ("s5", "证据与底稿管理", "#6fe0a0", ("evidence", "workpaper")),
        ("s6", "问题核查与定性", "#8cd07f", ("finding",)),
        ("s7", "报告与成果输出", "#b48ce0", ("report",)),
        ("s8", "整改跟踪与闭环", "#e08cc8", ("remedy",)),
    )
    assert tuple(nebula_graph.STAGES) == expected


def test_audit_pack_reproduces_the_trunk_and_layers() -> None:
    assert nebula_graph.TRUNK == [
        ("s1", "s2"), ("s2", "s3"), ("s3", "s4"), ("s4", "s5"),
        ("s5", "s6"), ("s6", "s7"), ("s7", "s8"), ("s8", "gov"), ("gov", "s1"),
    ]
    assert nebula_graph.GOV_SEGMENT == "govern"
    assert nebula_graph.BASE_SEGMENT == "foundation"
    assert nebula_graph.UNGROUPED_STAGE == "s0"
    assert nebula_graph.CONTROL_SEGMENTS == {
        "data-mask", "data-encrypt", "workflow-engine", "permission-control", "audit-log",
    }
    assert nebula_graph.STAGE_ORDER == ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]


def test_classify_still_works_for_audit_plugins() -> None:
    assert nebula_graph.classify("audit.mandate.demand-collect") == (LAYER_BIZ, "s1")
    assert nebula_graph.classify("audit.foundation.ocr-extract") == (LAYER_BASE, None)
    assert nebula_graph.classify("audit.govern.rule-iteration") == (LAYER_GOV, None)
    assert nebula_graph.classify("audit.zzz.unknown") == (LAYER_BIZ, "s0")


def test_default_domain_is_audit() -> None:
    """Omitting `domain` must keep the previous behaviour exactly."""
    plain = compose_flow(goal="全流程", select=sorted(discover_plugins()), plan_key="p")
    explicit = compose_flow(
        goal="全流程", select=sorted(discover_plugins()), plan_key="p", domain="audit",
    )
    assert {e["edge_id"] for e in plain["edges"]} == {e["edge_id"] for e in explicit["edges"]}


# -- discovery is no longer audit-only --------------------------------------

def test_discovery_defaults_to_the_audit_globs() -> None:
    specs = discover_plugins()
    assert specs and all(pid.startswith("audit.") for pid in specs)


def test_a_pack_globs_the_other_families() -> None:
    aiops = discover_plugins(globs=load_pack("aiops").globs)
    assert len(aiops) == 7
    assert all(pid.startswith("aiops.") for pid in aiops)
    # these were invisible to the planner while discovery was hardcoded
    assert "aiops.alert-triage" in aiops


def test_every_pack_on_disk_loads_and_validates() -> None:
    domains = available_domains()
    assert {"audit", "aiops"} <= set(domains)
    for domain in domains:
        pack = load_pack(domain)
        specs = discover_plugins(globs=pack.globs)
        folders = [d.name for d in PLUGIN_ROOT.glob(f"{domain}-*")]
        assert validate_pack(pack, plugin_ids=specs, folders=folders) == [], domain


def test_a_missing_pack_is_a_hard_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_pack("nope", root=tmp_path)


# -- the aiops pack ---------------------------------------------------------

def test_aiops_segments_collide_so_stages_are_pinned_explicitly() -> None:
    """`aiops.alert-triage` and `aiops.alert-correlation` both segment to
    `alert`; segment matching alone would put them in the same stage."""
    pack = load_pack("aiops")
    assert pack.classify("aiops.alert-triage") == (LAYER_BIZ, "a1")
    assert pack.classify("aiops.alert-correlation") == (LAYER_BIZ, "a2")
    assert pack.classify("aiops.rca-ranker") == (LAYER_BIZ, "a3")
    assert pack.classify("aiops.postmortem-draft") == (LAYER_BIZ, "a6")
    assert pack.classify("aiops.ticket-draft") == (LAYER_BIZ, "a6")


def test_aiops_composes_and_compiles_without_any_hand_written_chain() -> None:
    """The generality claim, executed: same composer, same compiler, different
    pack.  The five edges below are discovered from the contracts alone."""
    pack = load_pack("aiops")
    specs = discover_plugins(globs=pack.globs)
    flow = compose_flow(goal="告警处置闭环", select=sorted(specs), plan_key="aiops", domain="aiops")
    plan = compile_flow(flow)
    assert len(plan.nodes) == 7

    plugin_of = {n["node_instance_id"]: n["plugin_id"] for n in flow["nodes"]}
    edges = {
        (plugin_of[e["source_instance"]], plugin_of[e["target_instance"]], e["source_port"])
        for e in flow["edges"]
        if e.get("edge_class", "data") == "data"
    }
    assert edges == {
        ("aiops.alert-triage", "aiops.postmortem-draft", "incident-proposal"),
        ("aiops.alert-triage", "aiops.ticket-draft", "incident-proposal"),
        ("aiops.rca-ranker", "aiops.playbook-proposer", "rca-candidates"),
        ("aiops.playbook-proposer", "aiops.recovery-verifier", "remediation-proposal"),
        ("aiops.recovery-verifier", "aiops.postmortem-draft", "recovery-verification"),
    }


def test_aiops_domain_has_no_call_edges_because_none_are_declared() -> None:
    pack = load_pack("aiops")
    flow = compose_flow(
        goal="闭环", select=sorted(discover_plugins(globs=pack.globs)),
        plan_key="aiops", domain="aiops",
    )
    assert flow["wiring_report"]["call_edges"] == 0


# -- validation is fail-closed ---------------------------------------------

def _pack(**overrides) -> DomainPack:
    base = dict(
        domain="probe",
        globs=("probe-*",),
        stages=(Stage("p1", "一", 1, "#111111", ("alpha",)), Stage("p2", "二", 2, "#222222", ("beta",))),
        trunk=(("p1", "p2"),),
    )
    base.update(overrides)
    return DomainPack(**base)


def test_validation_rejects_a_duplicate_stage_key() -> None:
    pack = _pack(stages=(Stage("p1", "一", 1), Stage("p1", "又", 2)))
    assert any("duplicate stage key" in p for p in validate_pack(pack))


def test_validation_rejects_a_segment_claimed_by_two_stages() -> None:
    pack = _pack(stages=(Stage("p1", "一", 1, segments=("alpha",)), Stage("p2", "二", 2, segments=("alpha",))))
    assert any("claimed by both" in p for p in validate_pack(pack))


def test_validation_rejects_a_trunk_edge_naming_an_unknown_stage() -> None:
    pack = _pack(trunk=(("p1", "ghost"),))
    assert any("unknown stage" in p for p in validate_pack(pack))


def test_validation_rejects_an_override_pointing_at_an_unknown_stage() -> None:
    pack = _pack(stage_by_plugin={"probe.x": "ghost"})
    assert any("unknown stage" in p for p in validate_pack(pack))


def test_validation_rejects_globs_that_match_nothing() -> None:
    pack = _pack(globs=("nothing-*",))
    assert any("match none" in p for p in validate_pack(pack, folders=["probe-a", "probe-b"]))


def test_validation_rejects_an_override_for_a_plugin_outside_the_domain() -> None:
    pack = _pack(stage_by_plugin={"probe.x": "p1"})
    assert any("not in this domain" in p for p in validate_pack(pack, plugin_ids=["probe.y"]))


def test_a_plugin_in_a_layer_has_no_stage() -> None:
    pack = _pack(governance_segment="gov", support_segment="sup")
    assert pack.classify("probe.gov.thing") == (LAYER_GOV, None)
    assert pack.classify("probe.sup.thing") == (LAYER_BASE, None)


def test_an_unregistered_segment_falls_back_to_the_ungrouped_stage() -> None:
    pack = _pack(ungrouped_stage="p0")
    assert pack.classify("probe.unknown-thing") == (LAYER_BIZ, "p0")


def test_control_matching_accepts_either_the_segment_or_the_tail() -> None:
    pack = _pack(control_segments=frozenset({"data-mask"}))
    assert pack.is_control("probe.field.data-mask") is True
    assert pack.is_control("probe.field.other") is False


# -- the quant and knowledge packs -----------------------------------------
#
# These two families were the other half of the hardcoded-glob bug: fully
# implemented, contract-complete, and unreachable from the planner *and* from
# the graph — a pack file is what makes them discoverable at all.

OTHER_PACKS = {
    "quant": ("q1", "q2", "q3", "q4"),
    "knowledge": ("k1", "k2", "k3", "k4"),
}


@pytest.mark.parametrize("domain", sorted(OTHER_PACKS))
def test_the_remaining_packs_are_on_disk_and_discover_their_plugins(domain: str) -> None:
    pack = load_pack(domain)
    specs = discover_plugins(domain=domain)
    assert all(pid.startswith(f"{domain}.") for pid in specs)
    assert specs, f"{domain} composes nothing, so nothing can be planned with it"
    assert pack.stage_order == OTHER_PACKS[domain]
    # both families use two-segment ids whose tails vary a lot, so every plugin
    # is pinned explicitly rather than matched by segment
    assert set(pack.stage_by_plugin) == set(specs)
    assert set(pack.stage_of(pid) for pid in specs) == set(OTHER_PACKS[domain])


@pytest.mark.parametrize("domain", sorted(OTHER_PACKS))
def test_the_remaining_packs_compose_and_compile(domain: str) -> None:
    """Same composer, same compiler, a third and fourth pack — no new code."""
    specs = discover_plugins(domain=domain)
    flow = compose_flow(goal="全流程", select=sorted(specs), plan_key=domain, domain=domain)
    plan = compile_flow(flow)
    assert len(plan.nodes) == len(specs)


def test_discovering_an_unknown_domain_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown domain"):
        discover_plugins(domain="nope")
