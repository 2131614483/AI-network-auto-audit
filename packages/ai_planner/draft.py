"""CW5: strict GraphDraft validation — fail-closed before compilation.

The model output is untrusted input.  ``validate_draft`` enforces a closed
JSON shape (extra keys rejected), capability whitelist (recall list), and the
data boundary (only authorized seed sources may be referenced).  Any failure
is a deterministic safe-fail result; nothing here ever falls back to a fake
successful run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .catalog import PORT_CONTRACTS, CapabilityEntry
from .errors import CompileIssue

_ALLOWED_DRAFT_KEYS = {"nodes", "edges", "budget", "plan_key", "seed_inputs", "selection_reasons", "template"}
_ALLOWED_NODE_KEYS = {"node_instance_id", "capability", "plugin_id", "input_ports", "output_ports"}
# `edge_class` is accepted because the compiler accepts it: an AI draft of
# the whole network must be able to declare a portless `call` edge for a
# declared `invokes`, or it could never express the service layer at all.
_ALLOWED_EDGE_KEYS = {
    "edge_id", "source_instance", "source_port", "target_instance", "target_port",
    "adapter", "edge_class",
}
_ALLOWED_PORT_KEYS = {
    "port_id", "direction", "schema_ref", "schema_version", "schema_sha256",
    "media_type", "required", "cardinality", "classification", "transport",
}
_CONTRACT_FIELDS = (
    "schema_ref", "schema_version", "schema_sha256", "media_type",
    "required", "cardinality", "classification", "transport",
)
_ALLOWED_BUDGET_KEYS = {"max_chain_length", "max_candidates", "max_latency_ms"}
_MAX_DRAFT_NODES = 32
_MAX_REVISION_ROUNDS = 3


@dataclass(frozen=True, slots=True)
class DraftValidation:
    ok: bool
    draft: dict[str, Any] | None = None
    issue: CompileIssue | None = None


def _bad(code: str, message: str, **extra: str | None) -> DraftValidation:
    kwargs: dict[str, str | None] = dict(extra)
    suggested = kwargs.pop("suggested_action", None) or "revise the draft; the compiler cannot be bypassed"
    return DraftValidation(
        ok=False,
        issue=CompileIssue(
            code=code,
            message=message,
            node_id=kwargs.pop("node_id", None),
            port_id=kwargs.pop("port_id", None),
            edge_id=kwargs.pop("edge_id", None),
            suggested_action=suggested,
        ),
    )


def _validate_port_contract(
    port: dict[str, Any],
    node_id: str,
    direction: str,
    port_contracts: Mapping[str, dict[str, Any]] | None = None,
) -> DraftValidation | None:
    """Enforce the registered port contract directory (方案 5.1 / 6.1).

    Every port must carry the full required field set; a port name that is
    registered in :data:`PORT_CONTRACTS` must reproduce the registered values
    verbatim (except ``port_id``/``direction`` which are positional).  An
    unregistered port name is rejected: the recall list is the only source of
    acceptable ports — a draft can never widen its own permission by inventing
    a port.  Returns ``None`` when the port is acceptable.
    """
    port_id = port.get("port_id")
    if not isinstance(port_id, str) or not port_id.strip():
        return _bad("contract_mismatch", f"port of node {node_id} must declare port_id", node_id=node_id)
    if port.get("direction") != direction:
        return _bad(
            "contract_mismatch",
            f"port {port_id!r} of node {node_id} has direction {port.get('direction')!r}, expected {direction!r}",
            node_id=node_id,
            port_id=port_id,
            suggested_action='set direction to "input" or "output" exactly as the containing list',
        )
    for field in ("schema_ref", "schema_version", "schema_sha256", "media_type"):
        value = port.get(field)
        if not isinstance(value, str) or not value:
            return _bad(
                "contract_mismatch",
                f"port {port_id!r} of node {node_id} must declare {field}",
                node_id=node_id,
                port_id=port_id,
                suggested_action="copy the registered port contract verbatim from the capability list",
            )
    registry: Mapping[str, dict[str, Any]] = port_contracts if port_contracts is not None else PORT_CONTRACTS
    registered = registry.get(port_id)
    if registered is None:
        return _bad(
            "contract_mismatch",
            f"port {port_id!r} of node {node_id} is not in the registered contract directory",
            node_id=node_id,
            port_id=port_id,
            suggested_action="use only ports declared in the capability recall list",
        )
    # Compare only the fields the registry actually pins.  A directory-derived
    # registry deliberately leaves out `required`/`cardinality`: the same port id
    # can be produced as `one` and consumed as `many` (a merge input), so those
    # follow the direction, not the contract — and the compiler's fan-in gate is
    # their authority.  The hand-written registry pins the full set as before.
    for field, expected in registered.items():
        if port.get(field) != expected:
            return _bad(
                "contract_mismatch",
                f"port {port_id!r} of node {node_id} deviates from registered contract field {field} "
                f"({port.get(field)!r} != {expected!r})",
                node_id=node_id,
                port_id=port_id,
                suggested_action="copy the registered port contract verbatim from the capability list",
            )
    return None


def validate_draft(
    raw: Any,
    catalog: dict[str, CapabilityEntry],
    authorized_sources: set[tuple[str, str]],
    *,
    port_contracts: Mapping[str, dict[str, Any]] | None = None,
    max_nodes: int = _MAX_DRAFT_NODES,
) -> DraftValidation:
    """Validate an untrusted model draft against the closed schema + gates.

    ``port_contracts`` is the registry a port must be registered in.  It defaults
    to the hand-written :data:`PORT_CONTRACTS` (the legacy ledger/finding flow);
    pass a directory-derived one to validate a draft that plans a whole domain,
    otherwise every real port fails as "not in the registered contract
    directory" before the compiler is ever reached.
    """
    if not isinstance(raw, dict):
        return _bad("unsupported_feature", "draft is not a JSON object")
    unknown = set(raw) - _ALLOWED_DRAFT_KEYS
    if unknown:
        return _bad("unsupported_feature", f"unsupported draft field(s): {sorted(unknown)}")
    nodes = raw.get("nodes")
    edges = raw.get("edges")
    if not isinstance(nodes, list) or not nodes:
        return _bad("missing_required_input", "draft has no nodes")
    if not isinstance(edges, list):
        return _bad("unsupported_feature", "draft edges must be a list")
    if len(nodes) > max_nodes:
        return _bad("budget_exceeded", f"draft exceeds {max_nodes} nodes")

    for node in nodes:
        if not isinstance(node, dict):
            return _bad("unsupported_feature", "draft node is not an object")
        node_unknown = set(node) - _ALLOWED_NODE_KEYS
        if node_unknown:
            return _bad("unsupported_feature", f"unsupported node field(s): {sorted(node_unknown)}")
        capability = node.get("capability")
        node_id = node.get("node_instance_id")
        if not isinstance(node_id, str) or not node_id:
            return _bad("missing_required_input", "every node must declare node_instance_id")
        if not isinstance(capability, str) or capability not in catalog:
            return _bad(
                "capability_unavailable",
                f"capability not in recall list: {capability!r} (node {node_id})",
                node_id=node_id,
                suggested_action="choose a capability from the recall list; unknown plugins are never executed",
            )
        for port in node.get("input_ports") or ():
            if not isinstance(port, dict):
                return _bad("unsupported_feature", f"port on node {node_id} must be an object")
            if set(port) - _ALLOWED_PORT_KEYS:
                return _bad("unsupported_feature", f"unsupported port field(s) on node {node_id}")
            issue = _validate_port_contract(port, node_id, "input", port_contracts)
            if issue is not None:
                return issue
        for port in node.get("output_ports") or ():
            if not isinstance(port, dict):
                return _bad("unsupported_feature", f"port on node {node_id} must be an object")
            if set(port) - _ALLOWED_PORT_KEYS:
                return _bad("unsupported_feature", f"unsupported port field(s) on node {node_id}")
            issue = _validate_port_contract(port, node_id, "output", port_contracts)
            if issue is not None:
                return issue

    for edge in edges:
        if not isinstance(edge, dict):
            return _bad("unsupported_feature", "draft edge is not an object")
        edge_unknown = set(edge) - _ALLOWED_EDGE_KEYS
        if edge_unknown:
            return _bad("unsupported_feature", f"unsupported edge field(s): {sorted(edge_unknown)}")

    budget = raw.get("budget") or {}
    if not isinstance(budget, dict):
        return _bad("unsupported_feature", "draft budget must be an object")
    if set(budget) - _ALLOWED_BUDGET_KEYS:
        return _bad("unsupported_feature", f"unsupported budget field(s): {sorted(set(budget) - _ALLOWED_BUDGET_KEYS)}")

    seed_inputs = raw.get("seed_inputs")
    if seed_inputs is not None:
        if not isinstance(seed_inputs, list) or not all(isinstance(item, list) and len(item) == 2 for item in seed_inputs):
            return _bad("unsupported_feature", "seed_inputs must be a list of [node_instance_id, port_id] pairs")
        for pair in seed_inputs:
            # data boundary first: any reference outside the authorized set is denied,
            # even when the node itself does not exist yet (strongest safety ordering)
            if (pair[0], pair[1]) not in authorized_sources:
                return _bad(
                    "data_boundary_denied",
                    f"seed source ({pair[0]}, {pair[1]}) is outside the authorized data boundary",
                    node_id=pair[0],
                    port_id=pair[1],
                    suggested_action="only reference data sources the user authorized; out-of-boundary data is denied",
                )
            # An authorized seed source is a data outlet that already exists on
            # the canvas (an external data file the new flow consumes), so it
            # does NOT have to appear among this draft's freshly declared nodes.

    plan_key = raw.get("plan_key")
    if not isinstance(plan_key, str) or not plan_key.startswith("plan-"):
        return _bad("unsupported_feature", "plan_key must be a string starting with 'plan-'")

    return DraftValidation(ok=True, draft=raw)
