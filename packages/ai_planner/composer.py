"""CW7: deterministic AI workflow composer.

The composer is the "AI builds the workflow" engine: it reads the 100-plugin
contract directory, selects plugins (explicit or goal-keyword recall), wires
them by port contract (schema_ref equality), topologically orders the DAG,
declares seed inputs for dangling required ports and emits a flow JSON that
``compile_plan`` accepts.  No external model, no cloud API: composition is a
deterministic projection of the plugin directory.

Read-only by construction: it never executes, never writes artifacts and
never touches host state.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from packages.plugin_topology.compiler import compile_plan

if TYPE_CHECKING:  # both import this module, so the runtime imports are local
    from .domain_pack import DomainPack
    from .semantics import SemanticCatalog

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLUGIN_ROOT = PROJECT_ROOT / "plugins" / "builtin"
_SCHEMA_ROOT = PROJECT_ROOT / "contracts" / "jsonschema"


def schema_sha256(schema_ref: str) -> str:
    """Raw-byte sha256 of the schema file a ``schema_ref`` names.

    The field is overloaded: the shipped ``plugins/builtin`` protocols carry the
    **full file name** (``document-content.schema.json``), while the AI recall
    list in ``catalog.py`` uses the bare schema name (``document-content``).
    A bare name cannot simply be treated as a filename or it would be appended
    ``.schema.json`` a second time; detect the already-suffixed form and leave
    it alone.

    Fail-closed: a contract whose schema cannot be read is not silently given
    a placeholder — the placeholder (``"a"*64``) was the failure mode that
    let divergence go unnoticed until a runtime bind caught it.
    """
    name = schema_ref if schema_ref.endswith(".schema.json") else f"{schema_ref}.schema.json"
    path = _SCHEMA_ROOT / name
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise ValueError(f"contract schema missing for {schema_ref!r}: {name}") from exc


_PORT_FIELDS = {
    "schema_version": "1.0.0",
    "media_type": "application/json",
    "required": True,
    "classification": "internal",
    "transport": "artifact_ref",
}
_DEFAULT_BUDGET = {"max_chain_length": 128, "max_candidates": 5000, "max_latency_ms": 60000}


@dataclass(frozen=True, slots=True)
class PortSpec:
    port_id: str
    schema_ref: str
    direction: str
    required: bool = True
    #: ``one`` (default) — at most one producer may bind this input.
    #: ``many`` — this input aggregates several producers: a merge/collector
    #: port.  Carried through from the plugin contract instead of being
    #: hardcoded, because hardcoding it made fan-in structurally impossible: a
    #: node literally named "merge" could only ever receive one input, and the
    #: second candidate was silently reported as a duplicate producer.
    cardinality: str = "one"
    #: Explicit contract digest, for a contract that has no schema file on disk
    #: (a synthetic fixture, or an embedded contract).  ``None`` — the default,
    #: and what every shipped plugin uses — means "derive it from the schema
    #: file", which is the only value that can be *checked*.
    schema_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PluginSpec:
    plugin_id: str
    capability: str
    name: str
    description: str
    lifecycle: str
    domains: tuple[str, ...]
    inputs: tuple[PortSpec, ...]
    outputs: tuple[PortSpec, ...]
    invokes: tuple[str, ...] = ()


def discover_plugins(
    root: Path | None = None,
    lifecycle: str | None = None,
    *,
    globs: Iterable[str] | None = None,
    domain: str | None = None,
) -> dict[str, PluginSpec]:
    """Scan the plugin directory and return plugin_id -> PluginSpec.

    ``lifecycle="verified"`` restricts to runnable plugins; ``None`` returns the
    full contract directory.

    ``domain`` selects a domain pack by id (``audit`` / ``aiops`` / ``quant`` /
    ``knowledge``); its pack's ``discovery.globs`` scope the folder scan.  It is
    the public entry point's way of asking for one domain instead of the default
    audit pack.  ``globs`` is the low-level override used by the planner's
    recall engine; prefer ``domain`` everywhere else.
    """
    from .domain_pack import load_pack  # local: keeps import order obvious

    root = Path(root or DEFAULT_PLUGIN_ROOT)
    if globs is None and domain is not None:
        try:
            pack = load_pack(domain)
        except (OSError, ValueError) as exc:
            raise ValueError(f"unknown domain {domain!r}: {exc}") from exc
        globs = pack.globs
    patterns = tuple(globs) if globs is not None else load_pack().globs
    folders = sorted({folder for pattern in patterns for folder in root.glob(pattern)})
    specs: dict[str, PluginSpec] = {}
    for folder in folders:
        proto_path = folder / "plugin.protocol.json"
        if not proto_path.exists():
            continue
        try:
            proto = json.loads(proto_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if lifecycle is not None and proto.get("lifecycle") != lifecycle:
            continue
        capabilities = proto.get("capabilities") or []
        if not capabilities:
            continue
        cap = capabilities[0]
        inputs = tuple(
            PortSpec(
                port_id=str(item.get("contract_id") or "?"),
                schema_ref=str(item.get("schema_ref") or ""),
                direction="input",
                required=bool(item.get("required", True)),
                cardinality=str(item.get("cardinality") or "one"),
                schema_sha256=str(item["schema_sha256"]) if item.get("schema_sha256") else None,
            )
            for item in cap.get("inputs") or []
        )
        outputs = tuple(
            PortSpec(
                port_id=str(item.get("contract_id") or "?"),
                schema_ref=str(item.get("schema_ref") or ""),
                direction="output",
                schema_sha256=str(item["schema_sha256"]) if item.get("schema_sha256") else None,
            )
            for item in cap.get("outputs") or []
        )
        specs[str(proto.get("id") or folder.name)] = PluginSpec(
            plugin_id=str(proto.get("id") or folder.name),
            capability=str(cap.get("id") or folder.name),
            name=str(proto.get("name") or ""),
            description=str(proto.get("description") or ""),
            lifecycle=str(proto.get("lifecycle") or "contract_only"),
            domains=tuple(proto.get("domains") or []),
            inputs=inputs,
            outputs=outputs,
            invokes=tuple(str(x) for x in (cap.get("invokes") or [])),
        )
    return specs


_CJK = re.compile(r"[一-鿿]")


def _recall_terms(goal: str) -> list[tuple[str, int]]:
    """``(term, weight)`` pairs for deterministic recall.

    A whitespace/punctuation token is strong evidence (weight 2).  CJK text is
    normally written without separators, so ``"资金舞弊专项审计"`` would otherwise
    be a single token that matches nothing: character bigrams are added with
    weight 1 so such a goal still recalls the plugins whose descriptions mention
    资金 or 舞弊.  Bigrams alone would over-match (审/计 appears everywhere),
    which is exactly why they score below a full-token hit.
    """
    raw = [t for t in re.split(r"[\s，。、,;；：:！!？?（）()/\\\-]+", goal) if t]
    terms: list[tuple[str, int]] = []
    for token in raw:
        lowered = token.lower()
        terms.append((lowered, 2))
        if len(token) > 2 and _CJK.search(token):
            terms.extend(
                (token[i:i + 2], 1) for i in range(len(token) - 1)
                if _CJK.search(token[i:i + 2])
            )
    return terms


def recall_by_keywords(
    goal: str, specs: Mapping[str, PluginSpec], *, limit: int = 40,
) -> list[str]:
    """Deterministic keyword recall over name + description + capability."""
    terms = _recall_terms(goal)
    if not terms:
        return []
    scored: list[tuple[int, str]] = []
    for plugin_id, spec in specs.items():
        haystack = f"{spec.name} {spec.description} {spec.capability}".lower()
        score = sum(weight for term, weight in terms if term in haystack)
        if score > 0:
            scored.append((score, plugin_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [plugin_id for _, plugin_id in scored[:limit]]


def _slug(plugin_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", plugin_id.split(".")[-1].lower()).strip("-")


def _trailing_index(node_instance_id: str) -> int:
    """Occurrence index of an instance id (``x-002`` -> 2); 1 when absent."""
    _prefix, _sep, tail = node_instance_id.rpartition("-")
    return int(tail) if tail.isdigit() else 1


def _port_object(port: PortSpec) -> dict[str, Any]:
    return {
        "port_id": port.port_id,
        "direction": port.direction,
        "schema_ref": port.schema_ref,
        # Carried through from the contract: a merge port must be allowed to
        # aggregate, and the compiler's fan-in gate keys off this value.
        "cardinality": port.cardinality,
        # Derived from the schema file rather than defaulted: the compiler
        # requires every port to declare it, so leaving it to ``_PORT_FIELDS``
        # is how a composed flow ends up rejected wholesale as
        # ``contract_mismatch`` ("port ... must declare schema_sha256").
        "schema_sha256": port.schema_sha256 or schema_sha256(port.schema_ref),
        **_PORT_FIELDS,
    }


def _stage_rank(plugin_id: str, pack: DomainPack | None = None) -> int:
    """Position of a plugin on the pack's business-stage axis.

    Support-layer plugins rank 0 so they may feed any stage; governance ranks
    last.  Takes the pack explicitly because the ordering is domain knowledge:
    ranking an aiops plugin by the audit stage list would be meaningless.
    Lazy import: ``domain_pack`` is imported by ``nebula_graph``, which imports
    this module.
    """
    from .domain_pack import LAYER_BASE, LAYER_BIZ, load_pack

    pack = pack or load_pack()
    order = pack.stage_order
    layer, stage = pack.classify(plugin_id)
    if layer == LAYER_BASE:
        return 0
    if layer == LAYER_BIZ and stage is not None and stage in order:
        return order.index(stage) + 1
    return len(order) + 1


def _topo_sort(
    ordered: list[str],
    edges: list[dict[str, Any]],
) -> list[str]:
    indegree: dict[str, int] = {node_id: 0 for node_id in ordered}
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in ordered}
    for edge in edges:
        source = str(edge["source_instance"])
        target = str(edge["target_instance"])
        if source in indegree and target in indegree:
            adjacency[source].append(target)
            indegree[target] += 1
    result: list[str] = []
    queue = [node_id for node_id in ordered if indegree[node_id] == 0]
    queue.sort()
    while queue:
        node_id = queue.pop(0)
        result.append(node_id)
        for neighbor in sorted(adjacency[node_id]):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)
    if len(result) != len(ordered):
        cycle_nodes = sorted(set(ordered) - set(result))
        raise ComposeError(f"composed graph contains a cycle (nodes: {cycle_nodes})")
    return result


class ComposeError(ValueError):
    """Raised when the composed workflow cannot be built deterministically."""


def compose_flow(
    *,
    goal: str,
    select: Iterable[str] | None = None,
    chain: Iterable[tuple[str, str, str, str]] | None = None,
    plugin_root: Path | None = None,
    lifecycle: str | None = None,
    budget: dict[str, int] | None = None,
    plan_key: str | None = None,
    idempotency_key: str | None = None,
    semantics: SemanticCatalog | None = None,
    allow_schema_fallback: bool = False,
    instances: Mapping[str, int] | None = None,
    domain: str = "audit",
) -> dict[str, Any]:
    """Compose a workflow: select -> wire by contract -> order -> seed ports.

    ``chain`` optionally pins the main chain as explicit
    ``(source_plugin, source_port, target_plugin, target_port)`` edges at the
    highest priority.  Everything else is discovered: an output feeds an input
    when they name the **same business object** (exact ``contract_id``, or a
    reviewed alias) and the edge runs forward through the business stages.
    Returns a flow dict compatible with ``compile_plan``.

    ``allow_schema_fallback`` re-enables matching on ``schema_ref`` alone.  Off
    by default: it is a coarse compatibility type, and aligning on it produces
    semantically wrong edges (``ocr-image <- masked-set``).

    ``instances`` replicates a plugin — ``{"audit.audit.x": 3}`` yields three
    nodes with the same plugin id and distinct instance ids.  Which kind of
    reuse applies is a role question: a ``service`` is *called* from many places
    (one instance, N call edges) while a ``transform`` is *copied* (N instances,
    each with its own data chain).  A chain hint may address one instance either
    by its node instance id or by plugin id (which resolves to the first
    instance, recorded in ``wiring_report.ambiguous_chain_targets`` when there
    was a choice).

    ``domain`` selects the domain pack: it decides which plugin folders are
    discovered and which stage ordering adjudicates forward-only wiring.  The
    audit pack is the default and reproduces the previous hardcoded behaviour.
    """
    from .domain_pack import load_pack
    from .semantics import load_semantics

    pack = load_pack(domain)
    specs = discover_plugins(plugin_root, lifecycle=lifecycle, globs=pack.globs)
    if select is None:
        selected = recall_by_keywords(goal, specs)
        if not selected:
            raise ComposeError("no plugin matched the goal keywords")
    else:
        selected = list(select)
        unknown = [pid for pid in selected if pid not in specs]
        if unknown:
            raise ComposeError(f"unknown plugin ids: {unknown}")

    # node instances: stable slug + occurrence index.
    #
    # `instances` expands a plugin into N copies so a node can be **reused**
    # (one instance per audit sub-project, per sampling batch, …).  Which kind
    # of reuse a plugin needs is a role question: a `service` is called from many
    # places (one instance, N call edges) while a `transform` is copied
    # (N instances, each with its own data chain).  Expansion is by select order
    # so it is deterministic.
    if instances:
        expanded: list[str] = []
        for plugin_id in selected:
            count = int(instances.get(plugin_id, 1))
            if count < 1:
                raise ComposeError(f"instances for {plugin_id!r} must be >= 1, got {count}")
            expanded.extend([plugin_id] * count)
        selected = expanded

    # A slug collides only when *different* plugin ids share it.  Two instances
    # of the same plugin are the intended multi-instance case, not a clash —
    # counting occurrences instead of owners would rename every replicated
    # plugin's instances out from under the caller.
    slug_owners: dict[str, set[str]] = collections.defaultdict(set)
    for plugin_id in selected:
        slug_owners[_slug(plugin_id)].add(plugin_id)
    colliding_slugs = {slug for slug, owners in slug_owners.items() if len(owners) > 1}

    node_ids: list[str] = []
    instance_by_plugin: dict[str, int] = {}
    for plugin_id in selected:
        index = instance_by_plugin.get(plugin_id, 0) + 1
        instance_by_plugin[plugin_id] = index
        slug = _slug(plugin_id)
        if slug in colliding_slugs:
            # Two distinct plugin ids sharing a last segment
            # (`audit.field.workpaper-build` vs `audit.evidence.workpaper-build`)
            # would otherwise mint the same instance id twice and fail
            # compilation for a reason unrelated to the plan.  The shipped
            # directory has no such collision, so plans keep the ids they always
            # had; only a genuine clash pays for disambiguation.
            slug = f"{slug}-{hashlib.sha256(plugin_id.encode('utf-8')).hexdigest()[:6]}"
        node_ids.append(f"{slug}-{index:03d}")

    # ---- wiring ----------------------------------------------------------
    # Candidates are enumerated, then accepted in priority order with the cycle
    # test done *before* each acceptance.  Three things changed from the
    # previous three-pass sweep:
    #
    #  * priority order replaces traversal order.  The old code gave each input
    #    to whichever producer it happened to reach first while walking the
    #    selection list, so the caller's list order — not the semantics —
    #    decided who fed whom.
    #  * a candidate that would close a cycle is skipped and *recorded*, instead
    #    of being added and later bulk-deleted.  The old rollback removed only
    #    schema-fallback edges and, measured on the shipped directory, deleted
    #    all 44 of them: the fallback pass contributed nothing while looking
    #    like it did.
    #  * the schema fallback is off by default.  ``schema_ref`` is a coarse
    #    compatibility type (``artifact-ref.schema.json`` is produced by 5
    #    plugins), and aligning on it produces ``ocr-image <- masked-set``.  It
    #    stays available behind an explicit flag for experiments only.
    semantic = semantics if semantics is not None else load_semantics()

    edges: list[dict[str, Any]] = []
    claimed_counts: dict[tuple[str, str], int] = {}
    edge_index = 0

    # Inputs that aggregate several producers.  Read from the contract rather
    # than assumed, so a "merge" node can actually merge.
    port_cardinality: dict[tuple[str, str], str] = {}
    for node_id, plugin_id in zip(node_ids, selected):
        for port in specs[plugin_id].inputs:
            port_cardinality[(node_id, port.port_id)] = port.cardinality

    # pass 1: explicit chain hints (deterministic main chain, highest priority)
    chain = list(chain or ())
    ambiguous_targets: list[dict[str, Any]] = []

    def _resolve(token: str) -> str:
        """Resolve a chain-hint endpoint to exactly one node instance.

        A hint may name a **node instance id** (``finance-clean-002``) or a
        **plugin id**.  A plugin id with several instances resolves to the first
        — which used to happen silently, so a hint could never address the second
        instance and the plan looked fully wired while half of it was not.  The
        choice is recorded now instead of being invisible.
        """
        if token in node_ids:
            return token
        matches = [n for n, pid in zip(node_ids, selected) if pid == token]
        if not matches:
            raise ComposeError(f"chain hint references unknown plugin or instance: {token}")
        if len(matches) > 1:
            ambiguous_targets.append({
                "token": token, "resolved_to": matches[0], "candidates": len(matches),
            })
        return matches[0]

    for source_plugin, source_port, target_plugin, target_port in chain:
        source_node = _resolve(source_plugin)
        target_node = _resolve(target_plugin)
        source_plugin_id = selected[node_ids.index(source_node)]
        target_plugin_id = selected[node_ids.index(target_node)]
        source_spec = specs[source_plugin_id]
        target_spec = specs[target_plugin_id]
        output = next((o for o in source_spec.outputs if o.port_id == source_port), None)
        target = next((p for p in target_spec.inputs if p.port_id == target_port), None)
        if output is None or target is None:
            raise ComposeError(f"chain hint references unknown port: {source_plugin}.{source_port} -> {target_plugin}.{target_port}")
        if output.schema_ref != target.schema_ref:
            raise ComposeError(
                f"chain hint contract mismatch: {output.schema_ref} -> {target.schema_ref} "
                f"({source_plugin}.{source_port} -> {target_plugin}.{target_port})"
            )
        if claimed_counts.get((target_node, target_port), 0):
            raise ComposeError(f"chain hint input already claimed: {target_plugin}.{target_port}")
        edge_index += 1
        edges.append({
            "edge_id": f"e{edge_index:03d}",
            "source_instance": source_node,
            "source_port": output.port_id,
            "target_instance": target_node,
            "target_port": target.port_id,
        })
        claim_key = (target_node, target.port_id)
        claimed_counts[claim_key] = claimed_counts.get(claim_key, 0) + 1

    # pass 2: discovered edges, accepted by priority.
    # (priority, source_node, source_port, target_node, target_port, kind)
    candidates: list[tuple[int, str, str, str, str, str]] = []
    for source_pos, source_plugin in enumerate(selected):
        source_node = node_ids[source_pos]
        source_spec = specs[source_plugin]
        source_rank = _stage_rank(source_plugin, pack)
        for output in source_spec.outputs:
            if not output.schema_ref:
                continue  # untyped port cannot be matched, only explicitly chained
            for target_pos, target_plugin in enumerate(selected):
                if source_pos == target_pos:
                    continue
                # Forward-only: a data edge must not point back up the business
                # stages.  Measured on the shipped directory this rejects
                # nothing — all existing data edges already run forward or out
                # of the support layer — so it constrains future plans without
                # rewriting the current one.
                if source_rank > _stage_rank(target_plugin, pack):
                    continue
                target_node = node_ids[target_pos]
                for port in specs[target_plugin].inputs:
                    if output.schema_ref != port.schema_ref:
                        # Cross-schema edges need an explicit registered adapter;
                        # that is a deliberate act, never a guess.
                        continue
                    if output.port_id == port.port_id:
                        # An exact contract-id match is the strongest evidence:
                        # the producer names precisely what the input asks for.
                        priority, kind = 900, "exact"
                    elif semantic.canonical(output.port_id) == semantic.canonical(port.port_id):
                        # A reviewed alias is real but indirect, so it loses to
                        # an exact match competing for the same input.
                        priority, kind = 800, "alias"
                    elif allow_schema_fallback:
                        priority, kind = 600, "schema_fallback"
                    else:
                        continue
                    # When a plugin is replicated, pair instance k with instance
                    # k: that is what "run the same chain once per batch" means.
                    # The bonus is smaller than a match-kind tier, so it only
                    # breaks ties and never promotes a weaker kind of evidence.
                    if _trailing_index(source_node) == _trailing_index(target_node):
                        priority += 10
                    candidates.append(
                        (priority, source_node, output.port_id, target_node, port.port_id, kind)
                    )
    candidates.sort(key=lambda c: (-c[0], c[1], c[2], c[3], c[4]))

    dropped: list[dict[str, str]] = []
    edges_by_kind: dict[str, int] = {}
    for priority, source_node, source_port, target_node, target_port, kind in candidates:
        claim_key = (target_node, target_port)
        already = claimed_counts.get(claim_key, 0)
        if already and port_cardinality.get(claim_key, "one") != "many":
            # Single producer per input port — unless the port declares `many`,
            # which is how a merge/collector node receives several sources.
            # Losing candidates are recorded so the choice is auditable rather
            # than an artefact of iteration order.
            dropped.append({
                "source_instance": source_node, "source_port": source_port,
                "target_instance": target_node, "target_port": target_port,
                "reason": "dropped_duplicate_producer",
            })
            continue
        edge_index += 1
        candidate = {
            "edge_id": f"e{edge_index:03d}",
            "source_instance": source_node,
            "source_port": source_port,
            "target_instance": target_node,
            "target_port": target_port,
        }
        try:
            _topo_sort(node_ids, [*edges, candidate])
        except ComposeError:
            edge_index -= 1
            dropped.append({
                "source_instance": source_node, "source_port": source_port,
                "target_instance": target_node, "target_port": target_port,
                "reason": "dropped_cycle",
            })
            continue
        edges.append(candidate)
        claimed_counts[claim_key] = already + 1
        edges_by_kind[kind] = edges_by_kind.get(kind, 0) + 1

    ordered = _topo_sort(node_ids, edges)

    # pass 5: declared capability calls.  `capabilities[].invokes` is a
    # *capability invocation* — a reusable service called from many places — not
    # a dataflow, so it becomes a portless `call` edge.  Without this the 34
    # declarations in the shipped directory are dead weight: the service nodes
    # never enter the execution order and sit in the graph as isolated nodes.
    #
    # Direction: an edge ``A -> B`` means "B depends on A" (A runs first), the
    # same reading the data edges have.  So a caller invoking a service emits
    # ``service -> caller``: the caller depends on the capability it invokes.
    # Emitting the reverse would both run the call site before the thing it
    # calls and manufacture cycles against the data edges that already flow in
    # the dependency direction (measured: 2 spurious ``dropped_cycle``).
    call_edges: list[dict[str, Any]] = []
    dropped_calls: list[dict[str, str]] = []
    call_index = 0
    for source_pos, source_plugin in enumerate(selected):
        caller_node = node_ids[source_pos]
        for invoked in specs[source_plugin].invokes:
            called_node = next((n for n, pid in zip(node_ids, selected) if pid == invoked), None)
            if called_node is None or called_node == caller_node:
                continue
            call_index += 1
            candidate = {
                "edge_id": f"c{call_index:03d}",
                "source_instance": called_node,
                "source_port": "",
                "target_instance": caller_node,
                "target_port": "",
                "edge_class": "call",
            }
            try:
                _topo_sort(node_ids, [*edges, *call_edges, candidate])
            except ComposeError:
                dropped_calls.append({
                    "edge_id": candidate["edge_id"],
                    "source_instance": called_node,
                    "target_instance": caller_node,
                    "reason": "dropped_cycle",
                })
                continue
            call_edges.append(candidate)
    edges = [*edges, *call_edges]

    # node objects with the full port contract
    ordered_specs = {node_id: specs[selected[node_ids.index(node_id)]] for node_id in ordered}
    nodes: list[dict[str, Any]] = []
    for node_id in ordered:
        spec = ordered_specs[node_id]
        nodes.append({
            "node_instance_id": node_id,
            "plugin_id": spec.plugin_id,
            "capability": spec.capability,
            "input_ports": [_port_object(port) for port in spec.inputs],
            "output_ports": [_port_object(port) for port in spec.outputs],
        })

    # seed declaration: every required input without an incoming edge.
    # Only data edges can satisfy an input; a call edge is portless.
    has_incoming: set[tuple[str, str]] = {
        (edge["target_instance"], edge["target_port"])
        for edge in edges
        if edge.get("edge_class", "data") == "data"
    }
    spec_by_node = {node_id: specs[selected[node_ids.index(node_id)]] for node_id in node_ids}
    seed_inputs = sorted({
        (node_id, port.port_id)
        for node_id in node_ids
        for port in spec_by_node[node_id].inputs
        if port.required and (node_id, port.port_id) not in has_incoming
    })

    key = plan_key or "plan-ai-" + re.sub(r"[^a-z0-9]+", "-", goal.lower())[:40].strip("-")
    return {
        "plan_key": key,
        "goal": goal,
        "nodes": nodes,
        "edges": edges,
        "budget": dict(budget or _DEFAULT_BUDGET),
        "seed_inputs": seed_inputs,
        "chain": [list(edge) for edge in chain],
        "wiring_report": {
            "data_edges": len(edges) - len(call_edges),
            "call_edges": len(call_edges),
            "data_edges_by_kind": edges_by_kind,
            "dropped_data_edges": dropped,
            "dropped_call_edges": dropped_calls,
            "ambiguous_chain_targets": ambiguous_targets,
            "replicated_plugins": {
                plugin_id: count
                for plugin_id, count in instance_by_plugin.items()
                if count > 1
            },
            "seeds": len(seed_inputs),
        },
        "idempotency_key": idempotency_key or hashlib.sha256(json.dumps(
            {"goal": goal, "nodes": [n["node_instance_id"] for n in nodes],
             "edges": edges}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "composer_version": "1.2.0",
    }


def compile_flow(flow: dict[str, Any]) -> Any:
    """Deterministically compile a composed flow into an ExecutionPlan."""
    seeds: set[tuple[str, str]] = {tuple(item) for item in flow["seed_inputs"]}
    return compile_plan(
        nodes=flow["nodes"],
        edges=flow["edges"],
        budget=flow["budget"],
        plan_key=flow["plan_key"],
        seed_inputs=seeds,
    )


def compose_and_compile(
    *,
    goal: str,
    select: Iterable[str] | None = None,
    chain: Iterable[tuple[str, str, str, str]] | None = None,
    plugin_root: Path | None = None,
    lifecycle: str | None = None,
    budget: dict[str, int] | None = None,
    plan_key: str | None = None,
) -> tuple[dict[str, Any], Any]:
    """Compose then compile; raises ComposeError/CompileError on failure."""
    flow = compose_flow(
        goal=goal, select=select, chain=chain, plugin_root=plugin_root,
        lifecycle=lifecycle, budget=budget, plan_key=plan_key,
    )
    plan = compile_flow(flow)
    return flow, plan
