"""CW1 deterministic compiler: raw node/edge/port definitions -> ExecutionPlan.

Compilation is a strict gate, never a best-effort normalizer.  The check order
follows 方案 5.2:

1. draft schema: node kinds, port schema, supported fields (unknown fields are
   rejected, never silently ignored);
2. bindings: every edge's source/target node exists, every referenced port is
   declared, direction is output->input, and the contract matches (source
   ``schema_ref`` == target ``schema_ref``) unless an explicit registered
   adapter converts it;
3. required inputs: every ``required`` input port must have at least one
   incoming edge; a ``cardinality='one'`` input with more than one producer is
   a fan-in violation (list cardinality ``many`` is the only legal fan-in);
4. structural integrity: no duplicate ``node_instance_id``, no duplicate
   ``port_id`` within a node, no directed cycle;
5. budget: the node count must not exceed ``max_chain_length`` (and the other
   budget keys must be positive integers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .ir import EDGE_CLASS_DATA, EDGE_CLASSES, ExecutionPlan, IREdge, IRNode
from .ports import PortContract, validate_port

SUPPORTED_TOP_LEVEL_FIELDS = frozenset(
    {"nodes", "edges", "budget", "parameters", "layout", "plan_key", "version"}
)
SUPPORTED_NODE_FIELDS = frozenset(
    {"node_instance_id", "plugin_id", "capability", "input_ports", "output_ports", "parameters"}
)
SUPPORTED_EDGE_FIELDS = frozenset(
    {
        "edge_id", "source_instance", "source_port", "target_instance", "target_port",
        "adapter", "edge_class",
    }
)
# An adapter is an explicit contract conversion.  Registration is validated at
# execution time; the compiler only requires the declared adapter to be a
# non-empty name so the draft is not silently treated as a plain edge.
_MIN_ADAPTER_LENGTH = 1

_MAX_CHAIN_LENGTH_DEFAULT = 16

# Identifier safety.  ``plan_key`` and ``node_instance_id`` are AI-authored
# strings (the planner drafts them from a natural-language goal) and they
# become path segments under the staging root:
#
#     staging_root / "chains" / plan.plan_key / instance_id
#
# Without a charset gate a draft carrying ``plan-../../../../pwned`` compiles
# cleanly and the executor then ``mkdir``s outside the sandbox.  The compiler is
# the single choke point every plan passes through, so the check lives here;
# ``ports_executor`` keeps a resolved-containment assertion as defence in depth
# for plans that reach an executor without going through ``compile_plan``.
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_IDENTIFIER_LENGTH = 128
#: Identifiers naming the fields the message should quote back.
_IDENTIFIER_FIELDS = ("plan_key", "node_instance_id", "edge_id")


def validate_identifier(value: str, *, field: str) -> str:
    """Return ``value`` if it is a safe single path segment, else raise.

    Rejected: empty, over-long, a path separator, a leading ``.``, a ``..``
    sequence anywhere, a trailing ``.`` or space (Windows silently strips
    those, so two distinct ids could collide on one directory), or any
    character outside ``[A-Za-z0-9._-]``.
    """
    if not isinstance(value, str) or not value:
        raise CompileError(f"{field} must be a non-empty string")
    if len(value) > _MAX_IDENTIFIER_LENGTH:
        raise CompileError(
            f"{field} exceeds {_MAX_IDENTIFIER_LENGTH} characters: {value[:32]!r}…"
        )
    if not IDENTIFIER_PATTERN.match(value):
        raise CompileError(
            f"{field} must match [A-Za-z0-9][A-Za-z0-9._-]* "
            f"(no path separators or leading dot): {value!r}"
        )
    if ".." in value:
        raise CompileError(f"{field} must not contain '..': {value!r}")
    if value.endswith((".", " ")):
        raise CompileError(f"{field} must not end with '.' or a space: {value!r}")
    return value


class CompileError(ValueError):
    """The draft cannot compile; the reason is stable and test-assertable."""


@dataclass(frozen=True, slots=True)
class _Draft:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    budget: dict[str, int]
    parameters: dict[str, Any] | None
    layout: dict[str, Any] | None
    plan_key: str
    seed_inputs: frozenset[tuple[str, str]] = frozenset()


def compile_plan(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    budget: dict[str, int] | None = None,
    plan_key: str,
    parameters: dict[str, Any] | None = None,
    layout: dict[str, Any] | None = None,
    version: str = "1.0.0",
    seed_inputs: set[tuple[str, str]] | None = None,
) -> ExecutionPlan:
    """Compile a draft into an immutable ExecutionPlan or raise CompileError.

    ``seed_inputs`` declares the ``(node_instance_id, port_id)`` pairs whose
    input data is injected externally (方案 4: user goal + authorized data
    references); a required seed port is not a dangling input.
    """
    draft = _Draft(
        nodes=nodes,
        edges=edges,
        budget=dict(budget or {}),
        parameters=parameters,
        layout=layout,
        plan_key=plan_key,
        seed_inputs=frozenset(seed_inputs or ()),
    )
    _reject_unsupported_fields(draft)
    _reject_unsafe_identifiers(draft)
    _reject_duplicate_node_ids(draft)
    compiled_nodes = _compile_nodes(draft)
    compiled_edges = _compile_edges(draft, compiled_nodes)
    _reject_missing_required_inputs(compiled_nodes, compiled_edges, draft.seed_inputs)
    _reject_fan_in_violations(compiled_nodes, compiled_edges)
    _reject_cycles(compiled_nodes, compiled_edges)
    _reject_budget(draft, compiled_nodes)
    return ExecutionPlan(
        plan_key=plan_key,
        nodes=compiled_nodes,
        edges=compiled_edges,
        budget=dict(draft.budget),
        parameters=parameters,
        layout=layout,
        seed_inputs=draft.seed_inputs,
    )


def _reject_unsupported_fields(draft: _Draft) -> None:
    for node in draft.nodes:
        unknown = set(node) - SUPPORTED_NODE_FIELDS
        if unknown:
            raise CompileError(
                f"unsupported field(s) on node {node.get('node_instance_id', '?' )}: {sorted(unknown)}"
            )
    for edge in draft.edges:
        unknown = set(edge) - SUPPORTED_EDGE_FIELDS
        if unknown:
            raise CompileError(f"unsupported field(s) on edge {edge.get('edge_id', '?')}: {sorted(unknown)}")
    for node in draft.nodes:
        for port in [*node.get("input_ports", []), *node.get("output_ports", [])]:
            validate_port(port, node_instance_id=str(node.get("node_instance_id") or "?"))

def _reject_unsafe_identifiers(draft: _Draft) -> None:
    """Reject ids that would escape or corrupt the staging directory layout."""
    validate_identifier(str(draft.plan_key), field="plan_key")
    for node in draft.nodes:
        raw_instance = node.get("node_instance_id")
        if raw_instance is None:
            continue  # _reject_duplicate_node_ids reports the missing id
        validate_identifier(str(raw_instance), field="node_instance_id")
    for edge in draft.edges:
        raw_edge = edge.get("edge_id")
        if raw_edge is None:
            continue
        validate_identifier(str(raw_edge), field="edge_id")


def _reject_duplicate_node_ids(draft: _Draft) -> None:
    seen: set[str] = set()
    for node in draft.nodes:
        instance_id = str(node.get("node_instance_id") or "")
        if not instance_id.strip():
            raise CompileError("every node must declare node_instance_id")
        if instance_id in seen:
            raise CompileError(f"duplicate node_instance_id: {instance_id}")
        seen.add(instance_id)


def _compile_nodes(draft: _Draft) -> tuple[IRNode, ...]:
    compiled: list[IRNode] = []
    for node in draft.nodes:
        instance_id = str(node["node_instance_id"])
        plugin_id = str(node.get("plugin_id") or "")
        capability = str(node.get("capability") or "")
        if not plugin_id or not capability:
            raise CompileError(f"node {instance_id} must declare plugin_id and capability")
        input_ports = _compile_ports(node.get("input_ports", []), instance_id, "input")
        output_ports = _compile_ports(node.get("output_ports", []), instance_id, "output")
        compiled.append(
            IRNode(
                node_instance_id=instance_id,
                plugin_id=plugin_id,
                capability=capability,
                input_ports=input_ports,
                output_ports=output_ports,
                parameters=node.get("parameters") or None,
            )
        )
    return tuple(compiled)


def _compile_ports(raw_ports: list[Any], instance_id: str, direction: str) -> tuple[PortContract, ...]:
    seen: set[str] = set()
    compiled: list[PortContract] = []
    for raw in raw_ports:
        if not isinstance(raw, dict):
            raise CompileError(f"port on node {instance_id} must be an object")
        port = validate_port(raw, node_instance_id=instance_id)
        if port.direction != direction:
            raise CompileError(
                f"port {port.port_id!r} on node {instance_id} is declared in the wrong direction list"
            )
        if port.port_id in seen:
            raise CompileError(f"duplicate port_id {port.port_id!r} on node {instance_id}")
        seen.add(port.port_id)
        compiled.append(port)
    return tuple(compiled)


def _compile_edges(draft: _Draft, nodes: tuple[IRNode, ...]) -> tuple[IREdge, ...]:
    by_id = {node.node_instance_id: node for node in nodes}
    compiled: list[IREdge] = []
    for raw in draft.edges:
        edge_id = str(raw.get("edge_id") or "")
        if not edge_id.strip():
            raise CompileError("every edge must declare edge_id")
        source_instance = str(raw.get("source_instance") or "")
        source_port = str(raw.get("source_port") or "")
        target_instance = str(raw.get("target_instance") or "")
        target_port = str(raw.get("target_port") or "")
        if source_instance not in by_id:
            raise CompileError(f"edge {edge_id} references unknown node instance: {source_instance}")
        if target_instance not in by_id:
            raise CompileError(f"edge {edge_id} references unknown node instance: {target_instance}")
        source_node = by_id[source_instance]
        target_node = by_id[target_instance]
        edge_class = str(raw.get("edge_class") or EDGE_CLASS_DATA)
        if edge_class not in EDGE_CLASSES:
            raise CompileError(
                f"edge {edge_id} declares unknown edge_class {edge_class!r}; "
                f"expected one of {sorted(EDGE_CLASSES)}"
            )
        adapter = raw.get("adapter")
        if edge_class != EDGE_CLASS_DATA:
            # A non-data edge is a node-level constraint (a service invocation,
            # a scheduling dependency, a cross-domain bridge).  It carries no
            # artifact, so it must not name ports and must not declare an
            # adapter — either would imply a payload that does not exist.
            if source_port or target_port:
                raise CompileError(
                    f"edge {edge_id} of class {edge_class!r} must not declare ports "
                    f"({source_port!r} -> {target_port!r}); it carries no artifact"
                )
            if adapter is not None:
                raise CompileError(
                    f"edge {edge_id} of class {edge_class!r} must not declare an adapter"
                )
            adapter = None
        else:
            source = _find_port(source_node, source_port, "output")
            target = _find_port(target_node, target_port, "input")
            if adapter is not None:
                adapter = str(adapter)
                if len(adapter) < _MIN_ADAPTER_LENGTH:
                    raise CompileError(f"edge {edge_id} declares an empty adapter")
            elif source.schema_ref != target.schema_ref:
                raise CompileError(
                    f"edge {edge_id} contract mismatch: {source.schema_ref} -> {target.schema_ref} "
                    "requires an explicit adapter"
                )
        compiled.append(
            IREdge(
                edge_id=edge_id,
                source_instance=source_instance,
                source_port=source_port,
                target_instance=target_instance,
                target_port=target_port,
                adapter=adapter,
                edge_class=edge_class,
            )
        )
    return tuple(compiled)


def _find_port(node: IRNode, port_id: str, direction: str) -> PortContract:
    ports = node.output_ports if direction == "output" else node.input_ports
    for port in ports:
        if port.port_id == port_id:
            return port
    raise CompileError(
        f"edge references unknown {direction} port {port_id!r} on node {node.node_instance_id}"
    )


def _reject_missing_required_inputs(
    nodes: tuple[IRNode, ...],
    edges: tuple[IREdge, ...],
    seed_inputs: frozenset[tuple[str, str]],
) -> None:
    by_target: dict[tuple[str, str], int] = {}
    for edge in edges:
        if not edge.is_data:
            continue  # non-data edges carry no artifact and satisfy no input
        by_target[(edge.target_instance, edge.target_port)] = (
            by_target.get((edge.target_instance, edge.target_port), 0) + 1
        )
    for node in nodes:
        for port in node.input_ports:
            key = (node.node_instance_id, port.port_id)
            if port.required and key not in by_target and key not in seed_inputs:
                raise CompileError(
                    f"required input {port.port_id!r} of node {node.node_instance_id} has no incoming edge"
                )


def _reject_fan_in_violations(nodes: tuple[IRNode, ...], edges: tuple[IREdge, ...]) -> None:
    by_target: dict[tuple[str, str], int] = {}
    for edge in edges:
        if not edge.is_data:
            continue  # node-level fan-in (a service called from N places) is legal
        by_target[(edge.target_instance, edge.target_port)] = (
            by_target.get((edge.target_instance, edge.target_port), 0) + 1
        )
    for node in nodes:
        for port in node.input_ports:
            count = by_target.get((node.node_instance_id, port.port_id), 0)
            if count > 1 and port.cardinality != "many":
                raise CompileError(
                    f"fan-in onto port {port.port_id!r} of node {node.node_instance_id} "
                    "requires list cardinality (many)"
                )


def _reject_cycles(nodes: tuple[IRNode, ...], edges: tuple[IREdge, ...]) -> None:
    order = _topological_order(nodes, edges)
    if len(order) != len(nodes):
        raise CompileError("plan contains a directed cycle")


def _reject_budget(draft: _Draft, nodes: tuple[IRNode, ...]) -> None:
    budget = draft.budget
    for key in ("max_chain_length", "max_candidates", "max_latency_ms"):
        if key in budget:
            value = budget[key]
            if not isinstance(value, int) or value < 1:
                raise CompileError(f"budget {key} must be a positive integer")
    limit = int(budget.get("max_chain_length") or _MAX_CHAIN_LENGTH_DEFAULT)
    if len(nodes) > limit:
        raise CompileError(
            f"plan has {len(nodes)} nodes, exceeding max_chain_length budget {limit}"
        )


def topological_order(nodes: tuple[IRNode, ...], edges: tuple[IREdge, ...]) -> tuple[str, ...]:
    """Deterministic Kahn order by edge topology (nodes without edges keep the
    declaration order).  Raises CompileError on a cycle."""
    return _topological_order(nodes, edges)


def _topological_order(nodes: tuple[IRNode, ...], edges: tuple[IREdge, ...]) -> tuple[str, ...]:
    indegree: dict[str, int] = {node.node_instance_id: 0 for node in nodes}
    adjacency: dict[str, list[str]] = {node.node_instance_id: [] for node in nodes}
    for edge in edges:
        indegree[edge.target_instance] = indegree.get(edge.target_instance, 0) + 1
        adjacency[edge.source_instance].append(edge.target_instance)
    ready = [node.node_instance_id for node in nodes if indegree[node.node_instance_id] == 0]
    ready.sort()
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for target in sorted(adjacency[current]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    return tuple(order)
