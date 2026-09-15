"""Connectivity and provenance metrics for the audit plugin network.

Read-only measurement.  It never executes a plugin, never writes an artifact and
never touches host state: it either projects the on-disk contract directory
(:func:`connectivity_metrics`) or reads back a finished run's attempt records
(:func:`provenance_report`).

The graph definition is *always* explicit in the result.  Two edge sets exist
and they are not interchangeable:

``contract``
    producer output ``contract_id`` == consumer input ``contract_id``.  This is
    the only relation the compiler can turn into a data edge.
``contract+invokes``
    the above plus the declared ``invokes`` cross-layer capability calls.
    ``invokes`` is a capability call, not a dataflow, and the compiler cannot
    represent it as a plan edge (both endpoints must be ports) — so it is
    reported separately and never silently folded in.  Switching the definition
    mid-measurement is how a connectivity number gets inflated without anything
    actually connecting.

Why the numbers are reported the way they are: a coarse ``schema_ref`` is *not*
a semantic identity (``artifact-ref.schema.json`` is a catch-all produced by 5
plugins), so counting "same ``schema_ref``" matches as connectivity would raise
the metric while making the network wrong.  :func:`connectivity_metrics`
therefore counts only ``contract_id``-exact supply and demand, and reports dead
ends and unfillable inputs as first-class numbers instead of hiding them behind
an "edges" total.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .composer import PluginSpec
from .semantics import SemanticCatalog

EDGE_SET_CONTRACT = "contract"
EDGE_SET_CONTRACT_INVOKES = "contract+invokes"


# --------------------------------------------------------------------------
# contract supply / demand
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ContractIndex:
    """Which plugins produce and consume each ``contract_id``."""

    producers: Mapping[str, tuple[str, ...]]
    consumers: Mapping[str, tuple[str, ...]]
    input_ports_by_contract: Mapping[str, int]
    output_contract_count: int
    input_contract_count: int

    @property
    def reusable(self) -> tuple[str, ...]:
        """Contract ids that some plugin produces *and* some plugin consumes."""
        return tuple(sorted(k for k in self.producers if k in self.consumers))

    @property
    def dead_end_outputs(self) -> tuple[str, ...]:
        """Output contract ids no plugin consumes (terminal products)."""
        return tuple(sorted(k for k in self.producers if k not in self.consumers))

    @property
    def unfillable_inputs(self) -> tuple[str, ...]:
        """Input contract ids no plugin produces (must be seeded)."""
        return tuple(sorted(k for k in self.consumers if k not in self.producers))

    @property
    def unfillable_input_ports(self) -> int:
        """Input *ports* (not contract ids) that must be seeded."""
        return sum(self.input_ports_by_contract[k] for k in self.unfillable_inputs)


def build_contract_index(specs: Mapping[str, PluginSpec]) -> ContractIndex:
    producers: dict[str, list[str]] = collections.defaultdict(list)
    consumers: dict[str, list[str]] = collections.defaultdict(list)
    ports_by_contract: collections.Counter[str] = collections.Counter()
    for plugin_id in sorted(specs):
        spec = specs[plugin_id]
        for port in spec.outputs:
            if plugin_id not in producers[port.port_id]:
                producers[port.port_id].append(plugin_id)
        for port in spec.inputs:
            if plugin_id not in consumers[port.port_id]:
                consumers[port.port_id].append(plugin_id)
            ports_by_contract[port.port_id] += 1
    return ContractIndex(
        producers={k: tuple(v) for k, v in producers.items()},
        consumers={k: tuple(v) for k, v in consumers.items()},
        input_ports_by_contract=dict(ports_by_contract),
        output_contract_count=len(producers),
        input_contract_count=len(consumers),
    )


def contract_edges(specs: Mapping[str, PluginSpec]) -> set[tuple[str, str]]:
    """Directed ``(producer, consumer)`` pairs joined by contract_id equality."""
    index = build_contract_index(specs)
    edges: set[tuple[str, str]] = set()
    for contract_id, consumers in index.consumers.items():
        for source in index.producers.get(contract_id, ()):
            for target in consumers:
                if source != target:
                    edges.add((source, target))
    return edges


def invokes_edges(specs: Mapping[str, PluginSpec]) -> set[tuple[str, str]]:
    """Directed ``(caller, callee)`` pairs declared through ``invokes``.

    Not a dataflow: kept separate from :func:`contract_edges` on purpose.
    """
    edges: set[tuple[str, str]] = set()
    for plugin_id, spec in specs.items():
        for invoked in spec.invokes:
            if invoked != plugin_id and invoked in specs:
                edges.add((plugin_id, invoked))
    return edges


# --------------------------------------------------------------------------
# graph shape
# --------------------------------------------------------------------------

def _weakly_connected_components(
    nodes: Sequence[str], edges: Iterable[tuple[str, str]],
) -> list[int]:
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        if source in adjacency and target in adjacency:
            adjacency[source].append(target)
            adjacency[target].append(source)
    seen: set[str] = set()
    sizes: list[int] = []
    for node in nodes:
        if node in seen:
            continue
        stack = [node]
        seen.add(node)
        size = 0
        while stack:
            current = stack.pop()
            size += 1
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        sizes.append(size)
    sizes.sort(reverse=True)
    return sizes


@dataclass(frozen=True, slots=True)
class ConnectivityMetrics:
    """The shape of the network.  ``edge_set`` says which graph this describes."""

    edge_set: str
    plugins: int
    edges: int
    isolated_nodes: int
    no_incoming: int
    no_outgoing: int
    weakly_connected_components: int
    component_sizes: tuple[int, ...]
    dead_end_outputs: int
    unfillable_inputs: int
    reusable_contracts: int
    total_input_ports: int
    unfillable_input_ports: int
    seeds: int
    pure_seed_nodes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "edge_set": self.edge_set,
            "plugins": self.plugins,
            "edges": self.edges,
            "isolated_nodes": self.isolated_nodes,
            "no_incoming": self.no_incoming,
            "no_outgoing": self.no_outgoing,
            "weakly_connected_components": self.weakly_connected_components,
            "component_sizes": list(self.component_sizes),
            "dead_end_outputs": self.dead_end_outputs,
            "unfillable_inputs": self.unfillable_inputs,
            "reusable_contracts": self.reusable_contracts,
            "total_input_ports": self.total_input_ports,
            "unfillable_input_ports": self.unfillable_input_ports,
            "seeds": self.seeds,
            "pure_seed_nodes": self.pure_seed_nodes,
        }


def connectivity_metrics(
    specs: Mapping[str, PluginSpec],
    *,
    edges: Iterable[tuple[str, str]] | None = None,
    include_invokes: bool = False,
) -> ConnectivityMetrics:
    """Project the contract directory's connectivity.

    ``seeds`` and ``pure_seed_nodes`` are the two numbers that matter for the
    "is this a network or a fan of islands" question: ``seeds`` counts required
    input ports nothing produces (they *must* be injected), ``pure_seed_nodes``
    counts plugins with no incoming edge at all — a plugin that consumes nothing
    from any peer.
    """
    nodes = sorted(specs)
    if edges is None:
        selected = contract_edges(specs)
        edge_set = EDGE_SET_CONTRACT
        if include_invokes:
            selected = selected | invokes_edges(specs)
            edge_set = EDGE_SET_CONTRACT_INVOKES
    else:
        selected = set(edges)
        edge_set = EDGE_SET_CONTRACT_INVOKES if include_invokes else EDGE_SET_CONTRACT
    selected = {(s, t) for s, t in selected if s in specs and t in specs}

    index = build_contract_index(specs)
    outgoing: collections.Counter[str] = collections.Counter()
    incoming: collections.Counter[str] = collections.Counter()
    for source, target in selected:
        outgoing[source] += 1
        incoming[target] += 1

    sizes = _weakly_connected_components(nodes, selected)
    unfillable_ports = index.unfillable_input_ports
    return ConnectivityMetrics(
        edge_set=edge_set,
        plugins=len(nodes),
        edges=len(selected),
        isolated_nodes=sum(1 for n in nodes if not incoming[n] and not outgoing[n]),
        no_incoming=sum(1 for n in nodes if not incoming[n]),
        no_outgoing=sum(1 for n in nodes if not outgoing[n]),
        weakly_connected_components=len(sizes),
        component_sizes=tuple(sizes),
        dead_end_outputs=len(index.dead_end_outputs),
        unfillable_inputs=len(index.unfillable_inputs),
        reusable_contracts=len(index.reusable),
        total_input_ports=sum(len(specs[n].inputs) for n in nodes),
        unfillable_input_ports=unfillable_ports,
        seeds=unfillable_ports,
        pure_seed_nodes=sum(1 for n in nodes if not incoming[n]),
    )


# --------------------------------------------------------------------------
# run provenance
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProvenanceViolation:
    node_instance_id: str
    port_id: str
    reason: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "node_instance_id": self.node_instance_id,
            "port_id": self.port_id,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceReport:
    """Did every node really consume its upstream artifact, byte for byte?"""

    nodes: int
    total_bindings: int
    seed_bindings: int
    upstream_bindings: int
    verified: int
    nodes_with_upstream: int
    seed_only_nodes: int
    violations: tuple[ProvenanceViolation, ...]

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def upstream_share(self) -> float:
        if not self.total_bindings:
            return 0.0
        return self.upstream_bindings / self.total_bindings

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "total_bindings": self.total_bindings,
            "seed_bindings": self.seed_bindings,
            "upstream_bindings": self.upstream_bindings,
            "verified": self.verified,
            "nodes_with_upstream": self.nodes_with_upstream,
            "seed_only_nodes": self.seed_only_nodes,
            "ok": self.ok,
            "upstream_share": round(self.upstream_share, 4),
            "violations": [v.as_dict() for v in self.violations],
        }


def provenance_report(attempts: Iterable[Mapping[str, Any]]) -> ProvenanceReport:
    """Assert ``input sha256 == upstream output sha256`` across a finished run.

    ``attempts`` are the ``node_attempts`` records the run returns/persists:
    each must carry ``node_instance_id``, ``input_bindings`` and ``output_refs``,
    in the shape ``input_bindings[port_id] = {sha256, source_instance,
    source_port, uri, adapter}`` and ``output_refs[port_id] = {sha256, uri}``.

    A binding whose ``source_instance`` is ``"seed"`` is an injected external
    input and is counted, not verified — nothing upstream produced it.  A
    binding that crosses an adapter is likewise not sha-equal by design (the
    adapter re-serializes), so it is reported as ``adapter_binding`` rather than
    counted as a violation; its traceability comes from the adapter's own
    provenance field.
    """
    records = [r for r in attempts if isinstance(r, Mapping)]
    outputs: dict[str, Mapping[str, Any]] = {}
    for record in records:
        refs = record.get("output_refs")
        if isinstance(refs, Mapping):
            outputs[str(record.get("node_instance_id"))] = refs

    total = seed = upstream = verified = 0
    with_upstream = 0
    violations: list[ProvenanceViolation] = []
    for record in records:
        node_id = str(record.get("node_instance_id"))
        bindings = record.get("input_bindings")
        if not isinstance(bindings, Mapping):
            continue
        has_upstream = False
        for port_id, binding in bindings.items():
            if not isinstance(binding, Mapping):
                continue
            total += 1
            source = str(binding.get("source_instance"))
            if source == "seed":
                seed += 1
                continue
            has_upstream = True
            upstream += 1
            if binding.get("adapter"):
                # re-serialized on purpose: sha equality is not expected here
                violations.append(ProvenanceViolation(
                    node_id, str(port_id), "adapter_binding",
                    f"crosses adapter {binding.get('adapter')!r}; traceability via adapter provenance",
                ))
                continue
            source_refs = outputs.get(source, {})
            source_port = str(binding.get("source_port"))
            expected = None
            if isinstance(source_refs, Mapping):
                ref = source_refs.get(source_port)
                if isinstance(ref, Mapping):
                    expected = ref.get("sha256")
            if expected is None:
                violations.append(ProvenanceViolation(
                    node_id, str(port_id), "upstream_output_missing",
                    f"{source}.{source_port} produced no recorded output artifact",
                ))
            elif str(expected) == str(binding.get("sha256")):
                verified += 1
            else:
                violations.append(ProvenanceViolation(
                    node_id, str(port_id), "sha_mismatch",
                    f"binding {str(binding.get('sha256'))[:16]}… != {source}.{source_port} {str(expected)[:16]}…",
                ))
        if has_upstream:
            with_upstream += 1

    return ProvenanceReport(
        nodes=len(records),
        total_bindings=total,
        seed_bindings=seed,
        upstream_bindings=upstream,
        verified=verified,
        nodes_with_upstream=with_upstream,
        seed_only_nodes=len(records) - with_upstream,
        violations=tuple(violations),
    )


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

_LABELS: tuple[tuple[str, str], ...] = (
    ("plugins", "插件数"),
    ("edges", "边数"),
    ("isolated_nodes", "完全孤立节点"),
    ("no_incoming", "无入边节点"),
    ("no_outgoing", "无出边节点"),
    ("weakly_connected_components", "弱连通分量"),
    ("dead_end_outputs", "死端输出契约"),
    ("unfillable_inputs", "不可填输入契约"),
    ("unfillable_input_ports", "不可填输入端口"),
    ("total_input_ports", "输入端口总数"),
    ("seeds", "种子点"),
    ("pure_seed_nodes", "纯 seed 节点"),
    ("reusable_contracts", "可复用契约"),
)


def format_metrics_table(before: ConnectivityMetrics, after: ConnectivityMetrics) -> str:
    """Markdown before/after table for the archive."""
    lines = [
        "| 指标 | 改造前 | 改造后 | 变化 |",
        "| --- | --- | --- | --- |",
    ]
    for key, label in _LABELS:
        b = getattr(before, key)
        a = getattr(after, key)
        delta = a - b
        arrow = "" if delta == 0 else ("↓" if delta < 0 else "↑")
        lines.append(f"| {label} | {b} | {a} | {delta:+d} {arrow} |")
    if before.edge_set != after.edge_set:
        lines.append("")
        lines.append(
            f"> 注意：口径不同（{before.edge_set} → {after.edge_set}），"
            "该对比无效 —— 连通性指标只在同一图定义下可比。"
        )
    return "\n".join(lines)


def format_provenance_table(report: ProvenanceReport) -> str:
    lines = [
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 节点数 | {report.nodes} |",
        f"| 输入绑定总数 | {report.total_bindings} |",
        f"| 来自 seed | {report.seed_bindings} |",
        f"| 来自上游产出 | {report.upstream_bindings} |",
        f"| sha256 校验通过 | {report.verified} |",
        f"| 有上游输入的节点 | {report.nodes_with_upstream} |",
        f"| 纯 seed 节点 | {report.seed_only_nodes} |",
        f"| 违例 | {len(report.violations)} |",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# islands — explained by role, never silently zeroed
# --------------------------------------------------------------------------

ISLAND_EXTERNAL_INPUT = "external_input_by_design"
ISLAND_SUPPORT_UTILITY = "support_utility"
ISLAND_TERMINAL_PRODUCT = "terminal_product"
ISLAND_MISSING_UPSTREAM = "missing_upstream"
ISLAND_UNREVIEWED = "unreviewed"

ISLAND_CATEGORIES: tuple[str, ...] = (
    ISLAND_EXTERNAL_INPUT,
    ISLAND_SUPPORT_UTILITY,
    ISLAND_TERMINAL_PRODUCT,
    ISLAND_MISSING_UPSTREAM,
    ISLAND_UNREVIEWED,
)

ISLAND_NAMES_ZH: dict[str, str] = {
    ISLAND_EXTERNAL_INPUT: "按设计吃外部输入",
    ISLAND_SUPPORT_UTILITY: "被调用的支撑能力",
    ISLAND_TERMINAL_PRODUCT: "终端产物",
    ISLAND_MISSING_UPSTREAM: "真缺口：应有上游但无人提供",
    ISLAND_UNREVIEWED: "未判定角色，必须人工过目",
}


@dataclass(frozen=True, slots=True)
class IslandReason:
    """Why one plugin has no place on the data chain."""

    plugin_id: str
    role: str
    category: str
    reason: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "plugin_id": self.plugin_id,
            "role": self.role,
            "category": self.category,
            "category_zh": ISLAND_NAMES_ZH.get(self.category, self.category),
            "reason": self.reason,
            "detail": self.detail,
        }


def flow_edges_by_plugin(flow: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Map a composed flow's edges from node instances onto plugin ids.

    A flow names nodes by ``node_instance_id`` (``finance-clean-001``) while the
    contract directory names plugins (``audit.foundation.finance-clean``).
    Classifying islands without this translation silently reports *every* plugin
    as isolated — a measurement error that reads exactly like a connectivity
    collapse, which is why it lives here instead of in each caller.
    """
    nodes = flow.get("nodes") or []
    instance_to_plugin = {
        str(node["node_instance_id"]): str(node["plugin_id"])
        for node in nodes
    }
    pairs: set[tuple[str, str]] = set()
    for edge in flow.get("edges") or []:
        source = instance_to_plugin.get(str(edge.get("source_instance")))
        target = instance_to_plugin.get(str(edge.get("target_instance")))
        if source is not None and target is not None and source != target:
            pairs.add((source, target))
    return pairs


def classify_islands(
    specs: Mapping[str, PluginSpec],
    *,
    edges: Iterable[tuple[str, str]] | None = None,
    catalog: SemanticCatalog | None = None,
    pack_roles: Mapping[str, str] | None = None,
) -> tuple[IslandReason, ...]:
    """Explain every plugin that has no incident edge.

    The exchange rate here is honesty, not connectivity.  Earlier attempts to
    drive the island count down force-wired nodes by ``schema_ref`` similarity
    and produced ``ocr-image <- masked-set``; a node whose input is an external
    file, a permission request or a caller context has no place on a data chain
    and wiring one invents a dependency that does not exist.

    So a node with **no role review** (``ROLE_REVIEW``) is reported as
    ``unreviewed`` and counted, never quietly folded into a benign category, and
    a node whose role *does* expect an upstream is reported as
    ``missing_upstream`` — a real gap to be exposed, not smoothed over.

    ``pack_roles`` pins roles explicitly.  The classification rules and the role
    heuristics are separate concerns: the heuristics are deliberately
    conservative and expected to be superseded by a reviewed/AI-assisted pass,
    while these rules should hold regardless of how a role was decided.
    """
    from .roles import (  # local import: roles pulls in semantics
        ROLE_REVIEW,
        ROLE_SERVICE,
        ROLES_EXTERNAL_BY_DESIGN,
        resolve_all,
    )

    selected = set(edges) if edges is not None else contract_edges(specs)
    incident: set[str] = set()
    for source, target in selected:
        incident.add(source)
        incident.add(target)

    index = build_contract_index(specs)
    # A declared external input is a design decision, not a hole: the plugin is
    # *meant* to be fed from outside the chain.  Without this the declaration
    # was ignored and every declared-external port was still reported as
    # `missing_upstream`, which is the opposite of the truth and makes the
    # declaration worthless.
    declared_external = set(getattr(catalog, "external_inputs", ()) or ())
    unproducible = set(index.unfillable_inputs) - declared_external
    assignments = resolve_all(specs, catalog=catalog, pack_roles=pack_roles)

    islands: list[IslandReason] = []
    for plugin_id in sorted(specs):
        if plugin_id in incident:
            continue
        spec = specs[plugin_id]
        assignment = assignments[plugin_id]
        role = assignment.role
        inputs = [port.port_id for port in spec.inputs]
        outputs = [port.port_id for port in spec.outputs]

        if role == ROLE_REVIEW:
            islands.append(IslandReason(
                plugin_id, role, ISLAND_UNREVIEWED,
                "角色未判定：「终端产物」与「下游缺口」无法从契约目录区分",
                f"输出={','.join(outputs) or '无'}",
            ))
        elif role == ROLE_SERVICE:
            islands.append(IslandReason(
                plugin_id, role, ISLAND_SUPPORT_UTILITY,
                "被业务节点通过 invokes 调用，不占业务链位置",
                assignment.reason,
            ))
        elif role in ROLES_EXTERNAL_BY_DESIGN:
            islands.append(IslandReason(
                plugin_id, role, ISLAND_EXTERNAL_INPUT,
                "该角色按设计消费外部/人工输入，无同伴上游是声明行为",
                f"输入={','.join(inputs) or '无'}",
            ))
        else:
            # decision / transform / sink: these are expected to sit on the chain
            gap = sorted(set(inputs) & unproducible)
            if gap:
                islands.append(IslandReason(
                    plugin_id, role, ISLAND_MISSING_UPSTREAM,
                    "该角色应有上游，但下列输入契约全库无生产者",
                    f"无人产出: {','.join(gap)}",
                ))
            else:
                islands.append(IslandReason(
                    plugin_id, role, ISLAND_TERMINAL_PRODUCT,
                    "输入均可产出，但输出无人消费 —— 链条终点",
                    f"输出={','.join(outputs) or '无'}",
                ))
    return tuple(islands)


def island_summary(islands: Iterable[IslandReason]) -> dict[str, int]:
    counts: dict[str, int] = {category: 0 for category in ISLAND_CATEGORIES}
    for island in islands:
        counts[island.category] = counts.get(island.category, 0) + 1
    return counts
