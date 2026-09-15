"""Composition as a pipeline of named agents, with a trace of who produced what.

Why this exists: a single "composer" that silently does everything makes a plan
impossible to explain.  When a run produced 31 isolated nodes and a hand-written
main chain, nothing in the output said *which* step decided that — the wiring
looked like an oracle's verdict rather than a sequence of decisions.

So composition is split into agents with declared inputs and outputs, and every
step leaves a record in :class:`CompositionTrace`.  Two invariants carry over
from the existing planner:

* **an agent produces a draft, never an executable plan.**  ``compile_plan``
  remains the only authority, and it is called once, at the end, on the
  assembled flow.
* **every agent has a deterministic path.**  A model may be injected where
  judgment helps, but the default is deterministic code, so the pipeline is
  testable and CI never depends on a model being reachable.  An agent that ran
  on a model says so in the trace.

The agent roles mirror the ones the plan names: planning the skeleton, scouting
capabilities, weaving edges, critiquing them, auditing what is left over, and
curating contract proposals that only become real after human review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from .composer import (
    ComposeError,
    PluginSpec,
    compose_flow,
    discover_plugins,
    recall_by_keywords,
)
from .connectivity import (
    ISLAND_CATEGORIES,
    classify_islands,
    flow_edges_by_plugin,
    island_summary,
)
from .domain_pack import DomainPack, load_pack
from .errors import CompileIssue
from .roles import ROLE_NAMES_ZH, resolve_all
from .semantics import SemanticCatalog, load_semantics

AGENT_DOMAIN_PLANNER = "DomainPlanner"
AGENT_CAPABILITY_SCOUT = "CapabilityScout"
AGENT_CHAIN_WEAVER = "ChainWeaver"
AGENT_WIRING_CRITIC = "WiringCritic"
AGENT_ISLAND_AUDITOR = "IslandAuditor"
AGENT_CONTRACT_CURATOR = "ContractCurator"

AGENT_NAMES_ZH: dict[str, str] = {
    AGENT_DOMAIN_PLANNER: "领域规划",
    AGENT_CAPABILITY_SCOUT: "能力侦察",
    AGENT_CHAIN_WEAVER: "连边",
    AGENT_WIRING_CRITIC: "连边评审",
    AGENT_ISLAND_AUDITOR: "孤岛审计",
    AGENT_CONTRACT_CURATOR: "契约治理",
}


@dataclass(frozen=True, slots=True)
class Step:
    """One agent's contribution, recorded so the plan can be explained."""

    agent: str
    produced: str
    detail: Mapping[str, Any] = field(default_factory=dict)
    issues: tuple[CompileIssue, ...] = ()
    used_model: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "agent_zh": AGENT_NAMES_ZH.get(self.agent, self.agent),
            "produced": self.produced,
            "used_model": self.used_model,
            "detail": dict(self.detail),
            "issues": [
                {"code": i.code, "message": i.message, "suggested_action": i.suggested_action}
                for i in self.issues
            ],
        }


@dataclass(frozen=True, slots=True)
class CompositionTrace:
    """Which agent decided what — the plan's explanation, not a log."""

    goal: str
    domain: str
    steps: tuple[Step, ...]

    def step(self, agent: str) -> Step | None:
        return next((s for s in self.steps if s.agent == agent), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "domain": self.domain,
            "steps": [s.as_dict() for s in self.steps],
        }


@dataclass(frozen=True, slots=True)
class Composition:
    flow: dict[str, Any]
    trace: CompositionTrace


class DraftAgent(Protocol):
    """An agent that may consult a model instead of the deterministic path.

    Declared here so a model-backed implementation can be injected without this
    module importing any client, and so a test can assert the pipeline behaves
    identically with and without one.
    """

    name: str

    def run(self, context: Mapping[str, Any]) -> Mapping[str, Any]: ...


# --------------------------------------------------------------------------
# deterministic agents
# --------------------------------------------------------------------------

def plan_domain(
    goal: str,
    specs: Mapping[str, PluginSpec],
    pack: DomainPack,
    selected: Iterable[str] | None = None,
) -> Step:
    """Which stages of the pack the plan touches, and what it asks for.

    The skeleton is built from the plugins actually being planned — the explicit
    selection when there is one, otherwise the goal's keyword recall.  Using the
    recall regardless would report an empty skeleton whenever the caller passed
    an explicit selection, which reads as "this plan touches no stage" — the
    opposite of the truth.

    It does not invent stages: the skeleton is restricted to the pack's own
    stage list, so a goal can never widen the domain it is planned in.
    """
    recalled = recall_by_keywords(goal, specs, limit=len(specs))
    planned = list(selected) if selected is not None else recalled
    touched: dict[str, list[str]] = {key: [] for key in pack.stage_order}
    unstaged: list[str] = []
    for plugin_id in planned:
        _layer, stage = pack.classify(plugin_id)
        if stage is None:
            unstaged.append(plugin_id)
        elif stage in touched:
            touched[stage].append(plugin_id)
    skeleton = [
        {"stage": key, "name_zh": pack.stage_name.get(key, key), "plugins": sorted(touched[key])}
        for key in pack.stage_order
        if touched[key]
    ]
    return Step(
        agent=AGENT_DOMAIN_PLANNER,
        produced="stage_skeleton",
        detail={
            "domain": pack.domain,
            "stages": skeleton,
            "planned_count": len(planned),
            "recalled_count": len(recalled),
            "unstaged": sorted(unstaged),
        },
    )


def scout_capabilities(
    specs: Mapping[str, PluginSpec], catalog: SemanticCatalog, pack: DomainPack,
) -> Step:
    """The candidate set, with roles — the whitelist downstream agents may use."""
    assignments = resolve_all(specs, catalog=catalog)
    by_role: dict[str, int] = {}
    for assignment in assignments.values():
        by_role[assignment.role] = by_role.get(assignment.role, 0) + 1
    return Step(
        agent=AGENT_CAPABILITY_SCOUT,
        produced="candidates",
        detail={
            "count": len(specs),
            "by_role": {ROLE_NAMES_ZH.get(k, k): v for k, v in sorted(by_role.items())},
            "external_inputs": len(catalog.external_inputs),
        },
    )


def weave(flow: dict[str, Any]) -> Step:
    """The edges, and how each one was found."""
    report = flow["wiring_report"]
    return Step(
        agent=AGENT_CHAIN_WEAVER,
        produced="edges",
        detail={
            "data_edges": report["data_edges"],
            "data_edges_by_kind": dict(report["data_edges_by_kind"]),
            "call_edges": report["call_edges"],
            "dropped_data_edges": len(report["dropped_data_edges"]),
            "dropped_call_edges": len(report["dropped_call_edges"]),
            "seeds": report["seeds"],
        },
    )


def critique(flow: dict[str, Any], *, allow_schema_fallback: bool) -> Step:
    """Vetoes: what a reviewer would refuse to accept about these edges.

    Deterministic and conservative.  It does not remove edges — the weaver
    already refused the inadmissible ones; this states what a human should not
    mistake for evidence.
    """
    report = flow["wiring_report"]
    by_kind = report["data_edges_by_kind"]
    schema_only = int(by_kind.get("schema_fallback", 0))
    issues: list[CompileIssue] = []
    if schema_only:
        issues.append(CompileIssue(
            code="unsupported_feature",
            message=(
                f"{schema_only} 条边仅凭 schema_ref 相等连成；schema_ref 是粗粒度兼容类型，"
                "这类边语义不可信（实测会产出 ocr-image <- masked-set）"
            ),
            suggested_action="关闭 schema 兜底，或为这些端口补业务对象/别名后重连",
        ))
    return Step(
        agent=AGENT_WIRING_CRITIC,
        produced="vetoes",
        detail={
            "schema_fallback_enabled": allow_schema_fallback,
            "untrusted_schema_only_edges": schema_only,
            "cycle_dropped": sum(
                1 for d in report["dropped_data_edges"] if d["reason"] == "dropped_cycle"
            ),
            "duplicate_producer_dropped": sum(
                1 for d in report["dropped_data_edges"]
                if d["reason"] == "dropped_duplicate_producer"
            ),
        },
        issues=tuple(issues),
    )


def audit_islands(
    specs: Mapping[str, PluginSpec],
    flow: dict[str, Any],
    catalog: SemanticCatalog,
    *,
    pack_roles: Mapping[str, str] | None = None,
) -> Step:
    """What is left over, explained by role — never silently zeroed.

    ``pack_roles`` overrides which roles count; pass ``{}`` to see the
    heuristic-only picture, which is how the "unreviewed is reported, never
    zeroed" rule stays testable independently of how curated the shipped
    directory currently is.
    """
    islands = classify_islands(
        specs, edges=flow_edges_by_plugin(flow), catalog=catalog, pack_roles=pack_roles,
    )
    summary = island_summary(islands)
    present = {k: v for k, v in summary.items() if v}
    issues: list[CompileIssue] = []
    for island in islands:
        if island.category == "missing_upstream":
            issues.append(CompileIssue(
                code="missing_required_input",
                message=f"{island.plugin_id}: {island.detail}",
                node_id=island.plugin_id,
                suggested_action="补生产者，或声明该端口为外部输入",
            ))
    return Step(
        agent=AGENT_ISLAND_AUDITOR,
        produced="island_reasons",
        detail={
            "islands": len(islands),
            "by_category": {k: present[k] for k in ISLAND_CATEGORIES if k in present},
            "unreviewed": [i.plugin_id for i in islands if i.category == "unreviewed"],
        },
        issues=tuple(issues),
    )


def curate_contracts(catalog: SemanticCatalog) -> Step:
    """Alias proposals.  Suggested entries create no edges and need review."""
    suggested = [
        {"canonical": g.canonical, "members": list(g.members), "evidence": g.evidence}
        for g in catalog.suggested_alias_groups
    ]
    return Step(
        agent=AGENT_CONTRACT_CURATOR,
        produced="alias_proposals",
        detail={
            "confirmed_groups": len(catalog.alias_groups),
            "suggested_groups": len(suggested),
            "suggestions": suggested,
            "unreviewed_inputs": sorted(catalog.unreviewed_inputs),
        },
    )


# --------------------------------------------------------------------------
# the pipeline
# --------------------------------------------------------------------------

def _run(
    agent: str,
    produced: str,
    context: Mapping[str, Any],
    fallback: Callable[[], Step],
    overrides: Mapping[str, DraftAgent] | None,
) -> Step:
    """Run an override agent if one is injected, else the deterministic path.

    The override only ever sees a context dict and returns a detail dict: it
    cannot return edges, a plan or anything executable, so injecting a model
    cannot widen what the pipeline is allowed to produce.  Its output is still
    just a draft section, and ``compile_plan`` remains the authority.
    """
    override = (overrides or {}).get(agent)
    if override is None:
        return fallback()
    payload = override.run(context)
    detail = payload.get("detail") if isinstance(payload, Mapping) else None
    issues: tuple[CompileIssue, ...] = ()
    raw_issues = payload.get("issues") if isinstance(payload, Mapping) else None
    if isinstance(raw_issues, list):
        issues = tuple(
            CompileIssue(
                code=str(item.get("code") or "unsupported_feature"),
                message=str(item.get("message") or ""),
                suggested_action=str(item.get("suggested_action") or "revise the draft"),
            )
            for item in raw_issues
            if isinstance(item, Mapping)
        )
    return Step(
        agent=agent,
        produced=produced,
        detail=detail if isinstance(detail, Mapping) else {},
        issues=issues,
        used_model=True,
    )


def compose_with_trace(
    *,
    goal: str,
    select: Iterable[str] | None = None,
    chain: Iterable[tuple[str, str, str, str]] | None = None,
    domain: str = "audit",
    plugin_root: Path | None = None,
    plan_key: str | None = None,
    semantics: SemanticCatalog | None = None,
    allow_schema_fallback: bool = False,
    instances: Mapping[str, int] | None = None,
    agents: Mapping[str, DraftAgent] | None = None,
) -> Composition:
    """Run the agent pipeline and return the flow with its explanation.

    The weaver step is the existing deterministic composer; every other step
    reads its output.  Nothing here compiles — the caller does that, so the
    compile gate stays the single authority.

    ``agents`` optionally replaces individual steps with a model-backed
    :class:`DraftAgent`.  Absent that, every step runs deterministically, which
    is why CI never needs a model to exercise this path.
    """
    pack = load_pack(domain)
    catalog = semantics if semantics is not None else load_semantics()
    specs = discover_plugins(plugin_root, globs=pack.globs)
    if select is None:
        selected = recall_by_keywords(goal, specs)
        if not selected:
            raise ComposeError("no plugin matched the goal keywords")
    else:
        selected = list(select)

    flow = compose_flow(
        goal=goal, select=selected, chain=chain, plugin_root=plugin_root,
        plan_key=plan_key, semantics=catalog, domain=domain,
        allow_schema_fallback=allow_schema_fallback, instances=instances,
    )
    trace = CompositionTrace(
        goal=goal,
        domain=domain,
        steps=(
            _run(
                AGENT_DOMAIN_PLANNER, "stage_skeleton",
                {"goal": goal, "domain": pack.domain, "stages": list(pack.stage_order),
                 "selected": list(selected)},
                lambda: plan_domain(goal, specs, pack, selected), agents,
            ),
            _run(
                AGENT_CAPABILITY_SCOUT, "candidates",
                {"plugins": sorted(specs)},
                lambda: scout_capabilities(specs, catalog, pack), agents,
            ),
            _run(
                AGENT_CHAIN_WEAVER, "edges",
                {"wiring_report": flow["wiring_report"]},
                lambda: weave(flow), agents,
            ),
            _run(
                AGENT_WIRING_CRITIC, "vetoes",
                {"wiring_report": flow["wiring_report"],
                 "allow_schema_fallback": allow_schema_fallback},
                lambda: critique(flow, allow_schema_fallback=allow_schema_fallback), agents,
            ),
            _run(
                AGENT_ISLAND_AUDITOR, "island_reasons",
                {"nodes": [n["node_instance_id"] for n in flow["nodes"]]},
                lambda: audit_islands(specs, flow, catalog), agents,
            ),
            _run(
                AGENT_CONTRACT_CURATOR, "alias_proposals",
                {"unreviewed_inputs": sorted(catalog.unreviewed_inputs)},
                lambda: curate_contracts(catalog), agents,
            ),
        ),
    )
    return Composition(flow=flow, trace=trace)
