"""Draft workbench: compose a first draft, inspect it, edit it, validate it.

The loop this serves is: **produce a first draft → look at it → revise it (by
hand or by asking a model) → run contract validation**.  It is deliberately a
pure library: no server, no UI, no model client.  The local workbench app is a
thin HTTP shell over these functions, which is what makes the behaviour testable
without starting anything.

Three rules the code keeps:

* **`compile_plan` stays the only authority.**  Nothing here decides whether a
  graph is valid; it calls the compiler and reports what the compiler said.
  Validation does not need a model, so a hand-edited draft is validated
  immediately and offline.
* **An edit is fail-closed.**  `apply_edit` never returns a draft that does not
  compile; a rejected edit returns the reasons and leaves the draft untouched —
  the same "never fake a success" rule the planner follows.
* **Reuse is suggested, never assumed.**  A plugin whose role is reusable
  (validator/adapter/service) is *reported* with the places it could be inserted;
  wiring it stays a deliberate act.  Aligning on ``schema_ref`` alone would
  explode: ``artifact-ref.schema.json`` is a catch-all produced by five plugins.

Read-only apart from :func:`save_draft`, which writes only the file it is given.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from packages.plugin_topology.compiler import CompileError
from packages.plugin_topology.ports import PortContractError

from .catalog import build_port_contracts
from .composer import (
    PluginSpec,
    _slug,
    _trailing_index,
    compile_flow,
    compose_flow,
    discover_plugins,
    schema_sha256,
)
from .domain_pack import DomainPack, load_pack
from .errors import compile_issues, issues_to_dicts
from .roles import ROLE_ADAPTER, ROLE_SERVICE, ROLE_VALIDATOR, resolve_all
from .semantics import SemanticCatalog, load_semantics

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DRAFTS_ROOT = PROJECT_ROOT / ".data" / "drafts"

DRAFT_SCHEMA_VERSION = "1.0.0"

#: Roles whose whole point is being applied at more than one place.  A validator
#: takes any object and emits a verdict; an adapter normalises whatever it is
#: handed; a service is called from wherever it is useful.
REUSABLE_ROLES: tuple[str, ...] = (ROLE_VALIDATOR, ROLE_ADAPTER, ROLE_SERVICE)

#: Schema refs so generic that "same schema" is not evidence of anything.  The
#: suggestion list still shows them, flagged, because hiding them would be worse
#: than showing why they are weak.
COARSE_SCHEMAS: frozenset[str] = frozenset({
    "artifact-ref.schema.json", "document-content.schema.json",
})


# --------------------------------------------------------------------------
# the draft
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ValidationReport:
    """What the compiler said about a draft."""

    ok: bool
    issues: tuple[dict[str, Any], ...] = ()
    execution_hash: str = ""
    nodes: int = 0
    edges: int = 0
    seeds: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [dict(i) for i in self.issues],
            "execution_hash": self.execution_hash,
            "nodes": self.nodes,
            "edges": self.edges,
            "seeds": self.seeds,
        }


@dataclass(frozen=True, slots=True)
class EditResult:
    """An edit either produced a new draft or was refused with reasons."""

    ok: bool
    draft: dict[str, Any] | None = None
    issues: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "draft": self.draft,
            "issues": [dict(i) for i in self.issues],
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _specs_for(domain: str) -> dict[str, PluginSpec]:
    return discover_plugins(globs=load_pack(domain).globs)


def new_draft(
    domain: str,
    goal: str,
    *,
    select: Iterable[str] | None = None,
    chain: Iterable[tuple[str, str, str, str]] | None = None,
    instances: Mapping[str, int] | None = None,
    catalog: SemanticCatalog | None = None,
    plan_key: str | None = None,
) -> dict[str, Any]:
    """The first draft: the deterministic composition, plus its provenance."""
    pack = load_pack(domain)
    specs = _specs_for(domain)
    chosen = list(select) if select is not None else sorted(specs)
    flow = compose_flow(
        goal=goal, select=chosen, chain=chain, domain=domain,
        instances=instances, semantics=catalog or load_semantics(),
        plan_key=plan_key or f"plan-{domain}-draft",
    )
    draft = {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "domain": domain,
        "goal": goal,
        "origin": {
            "producer": "compose_flow",
            "composer_version": flow.get("composer_version", ""),
            "edited_by": ["compose"],
            "history": [{"at": _now(), "what": "first draft composed"}],
        },
        **{k: v for k, v in flow.items() if k != "wiring_report"},
    }
    # JSON-uniform: the composer yields (node, port) tuples, apply_edit yields
    # lists, and a draft is persisted as JSON — keep one shape.
    draft["seed_inputs"] = [[str(n), str(p)] for n, p in draft["seed_inputs"]]
    draft["suggestions"] = {"reusable_plugins": reusable_suggestions(draft, domain, pack=pack, specs=specs)}
    return draft


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate_draft_full(draft: Mapping[str, Any]) -> ValidationReport:
    """Two layers: the draft's own structure, then the compiler's gates.

    The structural layer exists because a hand-edited draft could name a port
    that no plugin declares; ``compile_plan`` validates a port against *the
    node's own declared ports*, so it would accept an invented port.  Checking
    the ports against the plugin directory closes that hole without duplicating
    any of the compiler's gates.
    """
    domain = str(draft.get("domain") or "audit")
    try:
        specs = _specs_for(domain)
    except FileNotFoundError as exc:
        return ValidationReport(False, (_issue("unsupported_feature", str(exc)),))

    structural = _structural_issues(draft, specs)
    if structural:
        return ValidationReport(False, tuple(structural))

    try:
        plan = compile_flow(dict(draft))
    except (CompileError, PortContractError) as exc:
        return ValidationReport(False, tuple(issues_to_dicts(compile_issues(exc))))
    return ValidationReport(
        True,
        execution_hash=plan.execution_hash,
        nodes=len(plan.nodes),
        edges=len(plan.edges),
        seeds=len(draft.get("seed_inputs") or ()),
    )


def _issue(code: str, message: str, **extra: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code, "message": message, "node_id": None,
        "port_id": None, "edge_id": None,
        "suggested_action": "revise the draft; the compiler cannot be bypassed",
    }
    payload.update(extra)
    return payload


def _structural_issues(
    draft: Mapping[str, Any], specs: Mapping[str, PluginSpec],
) -> list[dict[str, Any]]:
    """Every node must be a real plugin, and every port one it actually declares."""
    problems: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in draft.get("nodes") or ():
        node_id = str(node.get("node_instance_id") or "")
        plugin_id = str(node.get("plugin_id") or "")
        if not node_id:
            problems.append(_issue("missing_required_input", "node without node_instance_id"))
            continue
        if node_id in seen:
            problems.append(_issue("duplicate_node_instance_id", f"duplicate node {node_id}", node_id=node_id))
        seen.add(node_id)
        spec = specs.get(plugin_id)
        if spec is None:
            problems.append(_issue(
                "capability_unavailable",
                f"node {node_id} names plugin {plugin_id!r}, which is not in domain {draft.get('domain')!r}",
                node_id=node_id,
            ))
            continue
        for direction, declared in (("input", spec.inputs), ("output", spec.outputs)):
            allowed = {p.port_id for p in declared}
            for port in node.get(f"{direction}_ports") or ():
                port_id = str(port.get("port_id") or "")
                if port_id not in allowed:
                    problems.append(_issue(
                        "contract_mismatch",
                        f"node {node_id} declares unknown {direction} port {port_id!r}",
                        node_id=node_id, port_id=port_id,
                    ))
    return problems


# --------------------------------------------------------------------------
# editing
# --------------------------------------------------------------------------

def apply_edit(
    draft: Mapping[str, Any],
    edit: Mapping[str, Any],
    *,
    catalog: SemanticCatalog | None = None,
) -> EditResult:
    """Apply one human edit, fail-closed.

    A rejected edit returns the compiler's reasons and leaves ``draft``
    untouched — never a half-applied graph.  A successful edit is re-seeded
    (required inputs with no incoming edge become seeds, exactly as the composer
    does it), because a freshly added node legitimately has unbound inputs; the
    draft stays compilable and the UI shows the node as awaiting input.
    """
    kind = str(edit.get("kind") or "")
    working = json.loads(json.dumps(draft))  # deep copy: the caller's draft is never touched
    domain = str(working.get("domain") or "audit")
    specs = _specs_for(domain)
    history = working.setdefault("origin", {}).setdefault("history", [])

    try:
        if kind == "add_node":
            _add_node(working, edit, specs)
        elif kind == "remove_node":
            _remove_node(working, str(edit.get("node_instance_id") or ""))
        elif kind == "add_edge":
            _add_edge(working, edit)
        elif kind == "remove_edge":
            _remove_edge(working, str(edit.get("edge_id") or ""))
        elif kind == "set_instances":
            _set_instances(working, edit, specs)
        else:
            return EditResult(False, issues=(_issue(
                "unsupported_feature",
                f"unknown edit kind {kind!r}; expected add_node/remove_node/add_edge/"
                "remove_edge/set_instances",
            ),))
    except ValueError as exc:
        return EditResult(False, issues=(_issue("unsupported_feature", str(exc)),))

    _reseed(working)
    report = validate_draft_full(working)
    if not report.ok:
        return EditResult(False, issues=report.issues)

    working["origin"]["edited_by"] = list(dict.fromkeys([*working["origin"].get("edited_by", []), "human"]))
    history.append({"at": _now(), "what": kind, "detail": dict(edit)})
    working["suggestions"] = {
        "reusable_plugins": reusable_suggestions(working, domain, catalog=catalog, specs=specs),
    }
    return EditResult(True, draft=working)


def _plugin_of(node_id: str, draft: Mapping[str, Any]) -> str:
    for node in draft.get("nodes") or ():
        if node.get("node_instance_id") == node_id:
            return str(node.get("plugin_id"))
    raise ValueError(f"unknown node_instance_id {node_id!r}")


def _node_ids_of(draft: Mapping[str, Any], plugin_id: str) -> list[str]:
    return [
        str(n["node_instance_id"]) for n in draft.get("nodes") or ()
        if n.get("plugin_id") == plugin_id
    ]


def _node_object(plugin_id: str, node_id: str, spec: PluginSpec) -> dict[str, Any]:
    return {
        "node_instance_id": node_id,
        "plugin_id": plugin_id,
        "capability": spec.capability,
        "input_ports": [
            {
                "port_id": p.port_id, "direction": "input", "schema_ref": p.schema_ref,
                "cardinality": p.cardinality,
                "schema_version": "1.0.0", "schema_sha256": schema_sha256(p.schema_ref),
                "media_type": "application/json", "required": p.required,
                "classification": "internal", "transport": "artifact_ref",
            }
            for p in spec.inputs
        ],
        "output_ports": [
            {
                "port_id": p.port_id, "direction": "output", "schema_ref": p.schema_ref,
                "cardinality": p.cardinality,
                "schema_version": "1.0.0", "schema_sha256": schema_sha256(p.schema_ref),
                "media_type": "application/json", "required": p.required,
                "classification": "internal", "transport": "artifact_ref",
            }
            for p in spec.outputs
        ],
    }


def _next_node_id(draft: Mapping[str, Any], plugin_id: str) -> str:
    """Same id rule the composer uses: ``slug`` + the next free occurrence index."""
    existing = _node_ids_of(draft, plugin_id)
    index = max((_trailing_index(n) for n in existing), default=0) + 1
    return f"{_slug(plugin_id)}-{index:03d}"


def _add_node(draft: dict[str, Any], edit: Mapping[str, Any], specs: Mapping[str, PluginSpec]) -> None:
    plugin_id = str(edit.get("plugin_id") or "")
    spec = specs.get(plugin_id)
    if spec is None:
        raise ValueError(f"unknown plugin_id {plugin_id!r} in domain {draft.get('domain')!r}")
    node_id = str(edit.get("node_instance_id") or "") or _next_node_id(draft, plugin_id)
    if any(n.get("node_instance_id") == node_id for n in draft.get("nodes") or ()):
        raise ValueError(f"node_instance_id {node_id!r} already exists")
    draft.setdefault("nodes", []).append(_node_object(plugin_id, node_id, spec))


def _remove_node(draft: dict[str, Any], node_id: str) -> None:
    if not node_id:
        raise ValueError("remove_node requires node_instance_id")
    nodes = draft.get("nodes") or []
    remaining = [n for n in nodes if n.get("node_instance_id") != node_id]
    if len(remaining) == len(nodes):
        raise ValueError(f"unknown node_instance_id {node_id!r}")
    draft["nodes"] = remaining
    draft["edges"] = [
        e for e in draft.get("edges") or ()
        if e.get("source_instance") != node_id and e.get("target_instance") != node_id
    ]


def _port_schema(node: Mapping[str, Any], direction: str, port_id: str) -> str:
    for port in node.get(f"{direction}_ports") or ():
        if port.get("port_id") == port_id:
            return str(port.get("schema_ref") or "")
    raise ValueError(f"node {node.get('node_instance_id')} declares no {direction} port {port_id!r}")


def _add_edge(draft: dict[str, Any], edit: Mapping[str, Any]) -> None:
    source = str(edit.get("source_instance") or "")
    target = str(edit.get("target_instance") or "")
    source_port = str(edit.get("source_port") or "")
    target_port = str(edit.get("target_port") or "")
    adapter = edit.get("adapter") or None
    nodes = {str(n.get("node_instance_id")): n for n in draft.get("nodes") or ()}
    if source not in nodes:
        raise ValueError(f"unknown source_instance {source!r}")
    if target not in nodes:
        raise ValueError(f"unknown target_instance {target!r}")
    if source == target:
        raise ValueError("an edge must not loop a node onto itself")

    src_schema = _port_schema(nodes[source], "output", source_port)
    tgt_schema = _port_schema(nodes[target], "input", target_port)
    if src_schema != tgt_schema and not adapter:
        raise ValueError(
            f"contract mismatch: {src_schema} -> {tgt_schema} requires an explicit adapter "
            "(the compiler rejects a mismatched edge without one)"
        )

    edge_id = str(edit.get("edge_id") or "") or f"e{len(draft.get('edges') or ()) + 1:03d}"
    while any(e.get("edge_id") == edge_id for e in draft.get("edges") or ()):
        edge_id = f"{edge_id}x"
    if any(
        e.get("target_instance") == target and e.get("target_port") == target_port
        and (e.get("edge_class") or "data") == (edit.get("edge_class") or "data")
        for e in draft.get("edges") or ()
    ):
        raise ValueError(f"input {target}.{target_port} already has a producer")
    edge: dict[str, Any] = {
        "edge_id": edge_id,
        "source_instance": source,
        "source_port": source_port,
        "target_instance": target,
        "target_port": target_port,
    }
    if adapter:
        edge["adapter"] = str(adapter)
    if edit.get("edge_class"):
        edge["edge_class"] = str(edit["edge_class"])
    draft.setdefault("edges", []).append(edge)


def _remove_edge(draft: dict[str, Any], edge_id: str) -> None:
    if not edge_id:
        raise ValueError("remove_edge requires edge_id")
    edges = draft.get("edges") or []
    remaining = [e for e in edges if e.get("edge_id") != edge_id]
    if len(remaining) == len(edges):
        raise ValueError(f"unknown edge_id {edge_id!r}")
    draft["edges"] = remaining


def _set_instances(draft: dict[str, Any], edit: Mapping[str, Any], specs: Mapping[str, PluginSpec]) -> None:
    """Grow or shrink a plugin's instances **in place**, keeping existing wiring.

    Re-running the composer would be simpler but would throw away every manual
    edit — and "insert this reusable component at one more place" is exactly the
    edit a human is most likely to have made by hand.  New instances arrive
    unwired; :func:`_reseed` turns their inputs into seeds until they are wired,
    which is what the UI shows as "awaiting input".
    """
    plugin_id = str(edit.get("plugin_id") or "")
    spec = specs.get(plugin_id)
    if spec is None:
        raise ValueError(f"unknown plugin_id {plugin_id!r} in domain {draft.get('domain')!r}")
    raw_count = edit.get("count")
    if isinstance(raw_count, bool) or not isinstance(raw_count, (int, str)):
        raise ValueError("set_instances requires an integer count")
    try:
        count = int(raw_count)
    except (TypeError, ValueError) as exc:
        raise ValueError("set_instances requires an integer count") from exc
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")

    existing = sorted(_node_ids_of(draft, plugin_id), key=_trailing_index)
    if count > len(existing):
        for _ in range(len(existing), count):
            node_id = _next_node_id(draft, plugin_id)
            draft.setdefault("nodes", []).append(_node_object(plugin_id, node_id, spec))
        return
    for node_id in existing[count:]:
        _remove_node(draft, node_id)


def _reseed(draft: dict[str, Any]) -> None:
    """Required inputs with no incoming edge become seeds — the composer's rule.

    Without this every structural edit would be rejected: a newly added node has
    unbound required inputs by construction.
    """
    incoming = {
        (str(e.get("target_instance")), str(e.get("target_port")))
        for e in draft.get("edges") or ()
        if (e.get("edge_class") or "data") == "data"
    }
    seeds = sorted(
        (str(n["node_instance_id"]), str(p["port_id"]))
        for n in draft.get("nodes") or ()
        for p in n.get("input_ports") or ()
        if p.get("required", True) and (str(n["node_instance_id"]), str(p["port_id"])) not in incoming
    )
    draft["seed_inputs"] = [[n, p] for n, p in seeds]


# --------------------------------------------------------------------------
# reuse suggestions
# --------------------------------------------------------------------------

def reusable_suggestions(
    draft: Mapping[str, Any],
    domain: str,
    *,
    catalog: SemanticCatalog | None = None,
    specs: Mapping[str, PluginSpec] | None = None,
    pack: DomainPack | None = None,
) -> list[dict[str, Any]]:
    """Where each reusable plugin *could* be inserted — a proposal, not an edge.

    A validator takes any object and returns a verdict, so the honest answer to
    "where can it go" is a list of compatible producers, not a decision.  Nothing
    here touches ``edges``: the caller (human or AI) wires what it wants, via
    ``set_instances`` + ``add_edge``.
    """
    from .composer import _stage_rank  # local: private helper, same module family

    specs = specs if specs is not None else _specs_for(domain)
    pack = pack if pack is not None else load_pack(domain)
    roles = resolve_all(specs, catalog=catalog or load_semantics())

    produced: list[tuple[str, str, str, str]] = []  # (node, port, schema, plugin)
    for node in draft.get("nodes") or ():
        plugin_id = str(node.get("plugin_id") or "")
        for port in node.get("output_ports") or ():
            produced.append((
                str(node.get("node_instance_id")), str(port.get("port_id")),
                str(port.get("schema_ref") or ""), plugin_id,
            ))

    out: list[dict[str, Any]] = []
    for plugin_id in sorted(specs):
        assignment = roles.get(plugin_id)
        if assignment is None or assignment.role not in REUSABLE_ROLES:
            continue
        spec = specs[plugin_id]
        if not spec.inputs:
            continue
        target_rank = _stage_rank(plugin_id, pack)
        for port_in in spec.inputs:
            coarse = port_in.schema_ref in COARSE_SCHEMAS
            candidates = [
                {"node": node, "port": port, "schema_ref": schema,
                 "stage_ok": _stage_rank(producer_plugin, pack) <= target_rank}
                for node, port, schema, producer_plugin in produced
                if schema and schema == port_in.schema_ref
                and _plugin_of_safe(node, draft) != plugin_id
            ]
            if not candidates:
                continue
            out.append({
                "plugin_id": plugin_id,
                "role": assignment.role,
                "input_contract": port_in.port_id,
                "input_schema": port_in.schema_ref,
                "coarse_schema": coarse,
                "confidence": "coarse_schema_unverified" if coarse else "reviewed_schema",
                "candidates": candidates[:25],
                "candidate_count": len(candidates),
                "note": (
                    f"输入 schema {port_in.schema_ref} 是粗粒度兼容类型，"
                    "这些候选只在结构上相等，语义未经校验 —— 必须人工判断"
                    if coarse else "仅建议，不自动连；确认后用 set_instances + add_edge 接入"
                ),
            })
    # Trusted (narrow-schema) suggestions lead.  A coarse-schema hit list offered
    # as "the" candidates is how a validator ends up wired to an encrypted blob:
    # `artifact-ref.schema.json` matches almost everything on both sides.
    out.sort(key=lambda entry: (entry["coarse_schema"], -entry["candidate_count"], entry["plugin_id"]))
    return out


def _plugin_of_safe(node_id: str, draft: Mapping[str, Any]) -> str:
    try:
        return _plugin_of(node_id, draft)
    except ValueError:
        return ""


# --------------------------------------------------------------------------
# AI revision
# --------------------------------------------------------------------------

def domain_catalog(domain: str) -> dict[str, Any]:
    """The capability recall list a model needs to plan in this domain.

    Built from the plugin directory rather than a hand-written registry, so the
    model is offered exactly what exists.  Without it ``validate_draft`` rejects
    every capability the model names, and a revision can never succeed.
    """
    from .catalog import CapabilityEntry

    return {
        spec.capability: CapabilityEntry(
            capability=spec.capability,
            plugin_id=plugin_id,
            inputs=tuple(p.port_id for p in spec.inputs),
            outputs=tuple(p.port_id for p in spec.outputs),
        )
        for plugin_id, spec in _specs_for(domain).items()
    }

def revise_with_ai(
    draft: Mapping[str, Any],
    message: str,
    *,
    history: Iterable[Mapping[str, str]] | None = None,
    planner: Any | None = None,
    catalog: Mapping[str, Any] | None = None,
    budget: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Ask a model to revise this draft.  Fail-closed on every bad path.

    Returns the same envelope as :func:`apply_edit`: a new draft, or the reasons
    it could not be produced — **never** a silently unchanged draft presented as
    a success.  The model's output is still a draft: it goes through
    ``validate_draft`` and ``compile_plan`` inside ``AiPlanner`` like any other.

    ``planner`` is injectable so this is testable without a model being
    reachable.  When it is ``None`` the real one is built and a model outage
    surfaces as ``model_unavailable``.
    """
    from .chat import build_chat_goal

    goal = build_chat_goal(message, history=[dict(t) for t in (history or ())], base_draft=dict(draft))
    # `build_chat_goal` folds the existing draft into a *summary* (node count and
    # ids) because the canvas chat only needs to identify it.  A revision needs
    # the draft itself: without the edges the model cannot preserve them, and its
    # only honest answer to "revise this" is a gap.  So the JSON goes in too.
    goal += (
        "\n\n既有草稿（完整 JSON，请在其基础上修订并整体返回）：\n"
        + _draft_json_for_prompt(draft)
    )
    try:
        if planner is None:
            from .planner import AiPlanner, _default_llm

            planner = AiPlanner(_default_llm())
        domain = str(draft.get("domain") or "audit")
        specs = _specs_for(domain)
        derived, conflicts = build_port_contracts(specs)
        if conflicts:
            return EditResult(False, issues=(_issue(
                "contract_mismatch",
                f"端口契约目录有冲突，拒绝据此校验：{conflicts}",
                suggested_action="先修掉插件目录里同 contract_id 不同 schema_ref 的端口",
            ),)).as_dict()
        # The draft's own seeds are its external inputs, so they are exactly the
        # data sources this revision is allowed to reference.  Passing an empty
        # set would deny every seed the current draft already declares.
        authorized = {(str(n), str(p)) for n, p in (draft.get("seed_inputs") or ())}
        outcome = planner.plan(
            goal=goal,
            catalog=domain_catalog(domain),
            authorized_sources=authorized,
            budget=dict(budget) if budget else None,
            port_contracts=derived,
            max_draft_nodes=len(specs) * 2,
        )
    except Exception as exc:  # noqa: BLE001 - an outage must not look like a revision
        return EditResult(False, issues=(_issue(
            "model_unavailable",
            f"模型不可用：{type(exc).__name__}: {exc}",
            suggested_action="配置可用的模型通道后重试；草稿未被改动",
        ),)).as_dict()

    if outcome.status != "draft_ready" or outcome.draft is None:
        # An empty draft is the model *saying it cannot do this* — the planner
        # prompt explicitly allows a `plan-…` gap answer.  Reporting that as
        # "draft has no nodes" (the validator's phrasing) would read like a
        # malformed answer, when it is in fact an honest refusal, and the reason
        # the request was unsatisfiable is what the human needs to see.
        draft = outcome.draft if isinstance(outcome.draft, dict) else {}
        if not draft.get("nodes"):
            return EditResult(False, issues=(_issue(
                "capability_unavailable",
                "模型判定该要求在当前契约下无法满足（返回空草稿）；"
                "常见原因是两侧端口 schema_ref 不相等、需要显式 adapter",
                suggested_action="换一个要求，或先手工把这条边连上（必要时声明 adapter）",
            ),)).as_dict()
        return EditResult(False, issues=tuple(outcome.issues) or (_issue(
            "capability_unavailable", "模型未能产出一个可编译的草稿",
        ),)).as_dict()

    revised = json.loads(json.dumps(draft))
    revised["nodes"] = outcome.draft.get("nodes") or []
    revised["edges"] = outcome.draft.get("edges") or []
    if outcome.draft.get("budget"):
        revised["budget"] = outcome.draft["budget"]
    _reseed(revised)
    report = validate_draft_full(revised)
    if not report.ok:
        return EditResult(False, issues=report.issues).as_dict()
    revised["origin"]["edited_by"] = list(dict.fromkeys([*revised["origin"].get("edited_by", []), "ai"]))
    revised["origin"]["history"].append({"at": _now(), "what": "ai-revision", "detail": {"message": message}})
    return EditResult(True, draft=revised).as_dict()


def _draft_json_for_prompt(draft: Mapping[str, Any], *, max_chars: int = 60_000) -> str:
    """The draft as compact JSON for the model, truncated loudly if enormous.

    A truncated payload would silently change what "revise this" means, so the
    marker says exactly how much was withheld and the caller can refuse.
    """
    payload = json.dumps(
        {"plan_key": draft.get("plan_key"), "nodes": draft.get("nodes"),
         "edges": draft.get("edges"), "budget": draft.get("budget"),
         "seed_inputs": draft.get("seed_inputs")},
        ensure_ascii=False, separators=(",", ":"),
    )
    if len(payload) <= max_chars:
        return payload
    return payload[:max_chars] + (
        f"\n…（草稿过长已截断 {len(payload) - max_chars} 字符；"
        "截断后无法保证修订完整，请改用更窄的 select）"
    )


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def save_draft(name: str, draft: Mapping[str, Any], *, root: Path | None = None) -> Path:
    """Write the draft — the only file this module ever writes."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"unsafe draft name {name!r}")
    base = Path(root) if root is not None else DEFAULT_DRAFTS_ROOT
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{name}.json"
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def load_draft(name: str, *, root: Path | None = None) -> dict[str, Any]:
    base = Path(root) if root is not None else DEFAULT_DRAFTS_ROOT
    path = base / f"{name}.json"
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def list_drafts(*, root: Path | None = None) -> list[str]:
    base = Path(root) if root is not None else DEFAULT_DRAFTS_ROOT
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.json"))
