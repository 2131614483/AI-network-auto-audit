"""CW1 execution IR: the single executable intermediate representation.

The IR is the only form every executor consumes (方案 5.0 ``ExecutionPlan``).
It decouples execution definition from presentation: ``layout`` is carried
but excluded from ``execution_hash``, so dragging nodes on a canvas never
changes the plan identity (方案 CW1 exit: layout does not change the hash).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .ports import PortContract


@dataclass(frozen=True, slots=True)
class IRNode:
    """One node instance in the plan.

    ``node_instance_id`` is unique per plan and independent of the plugin /
    blueprint / capability ids: the same plugin may appear many times with
    distinct instance ids (方案 3.2).
    """

    node_instance_id: str
    plugin_id: str
    capability: str
    input_ports: tuple[PortContract, ...] = ()
    output_ports: tuple[PortContract, ...] = ()
    parameters: dict[str, Any] | None = None

    def _hash_payload(self) -> dict[str, object]:
        return {
            "node_instance_id": self.node_instance_id,
            "plugin_id": self.plugin_id,
            "capability": self.capability,
            "input_ports": [port.as_dict() for port in self.input_ports],
            "output_ports": [port.as_dict() for port in self.output_ports],
            "parameters": self.parameters or {},
        }


EDGE_CLASS_DATA = "data"
EDGE_CLASS_CALL = "call"
EDGE_CLASS_CONTROL = "control"
EDGE_CLASS_BRIDGE = "bridge"

EDGE_CLASSES: tuple[str, ...] = (
    EDGE_CLASS_DATA, EDGE_CLASS_CALL, EDGE_CLASS_CONTROL, EDGE_CLASS_BRIDGE,
)

#: Edge classes that carry no artifact: they are node-level ordering /
#: participation constraints, so their ``source_port``/``target_port`` are empty
#: and they never satisfy a required input.
NON_DATA_EDGE_CLASSES: frozenset[str] = frozenset(
    {EDGE_CLASS_CALL, EDGE_CLASS_CONTROL, EDGE_CLASS_BRIDGE}
)


@dataclass(frozen=True, slots=True)
class IREdge:
    """One directed edge between two node instances.

    ``edge_class='data'`` (the default, and the only kind that existed before)
    is bound by ``(node_instance_id, port_id)`` on both ends.  Fan-out (one
    producer port, many consumers) is legal; fan-in onto a ``cardinality='one'``
    input is rejected by the compiler unless an explicit list
    (``cardinality='many'``) is declared.  An ``adapter`` is an explicit,
    registered contract conversion; without one the source and target schema
    refs must match exactly (方案 5.1: never rename a contract and call it a
    different contract).

    ``edge_class='call'`` represents a declared capability invocation
    (``capabilities[].invokes``) — a **reusable service called from many
    places**.  It carries no artifact, so its ports are empty and it must never
    be counted when deciding whether a required input is satisfied.  It does
    order execution (the service runs before its caller) and it makes the
    service a real member of the graph instead of an isolated node.

    ``edge_class='control'`` is scheduling/sequencing (a scheduler node gating
    others); ``'bridge'`` is a cross-domain connection.  Both are likewise
    port-less.
    """

    edge_id: str
    source_instance: str
    source_port: str
    target_instance: str
    target_port: str
    adapter: str | None = None
    edge_class: str = EDGE_CLASS_DATA

    @property
    def is_data(self) -> bool:
        return self.edge_class == EDGE_CLASS_DATA

    def _hash_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "edge_id": self.edge_id,
            "source_instance": self.source_instance,
            "source_port": self.source_port,
            "target_instance": self.target_instance,
            "target_port": self.target_port,
            "adapter": self.adapter,
        }
        # Only non-default classes enter the hash, so a plan made purely of data
        # edges hashes byte-for-byte as it did before edge classes existed.
        if self.edge_class != EDGE_CLASS_DATA:
            payload["edge_class"] = self.edge_class
        return payload


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Compiled, deterministic execution IR."""

    plan_key: str
    nodes: tuple[IRNode, ...]
    edges: tuple[IREdge, ...]
    budget: dict[str, int]
    parameters: dict[str, Any] | None = None
    layout: dict[str, Any] | None = None
    execution_hash: str = ""
    #: ``(node_instance_id, port_id)`` pairs whose input is injected from
    #: outside the plan (a canvas data outlet) rather than from an edge.  The
    #: executor needs the caller to supply an artifact for each such key, so the
    #: plan must carry them: a caller that has to guess the key (as the demo
    #: script did with a hardcoded node name) binds nothing when the planner
    #: named its nodes differently, and the run then fails for the wrong reason.
    #:
    #: Deliberately excluded from ``execution_hash``: the hash identifies the
    #: node/edge structure, and folding in the injection points would change
    #: every pre-existing plan hash.
    seed_inputs: frozenset[tuple[str, str]] = frozenset()

    def __post_init__(self) -> None:
        if not self.execution_hash:
            object.__setattr__(self, "execution_hash", _execution_hash(self))

    def seed_keys(self, port_id: str | None = None) -> tuple[tuple[str, str], ...]:
        """Deterministic ``(node_instance_id, port_id)`` pairs needing injection.

        Optionally filtered by port id, so a caller that holds one artifact for
        a known port can bind exactly the nodes that consume it.
        """
        keys = self.seed_inputs if port_id is None else frozenset(
            key for key in self.seed_inputs if key[1] == port_id
        )
        return tuple(sorted(keys))

    def node_by_id(self, node_instance_id: str) -> IRNode:
        for node in self.nodes:
            if node.node_instance_id == node_instance_id:
                return node
        raise KeyError(f"unknown node instance: {node_instance_id}")

    def outgoing_edges(self, node_instance_id: str) -> tuple[IREdge, ...]:
        return tuple(e for e in self.edges if e.source_instance == node_instance_id)

    def incoming_edges(self, node_instance_id: str) -> tuple[IREdge, ...]:
        return tuple(e for e in self.edges if e.target_instance == node_instance_id)


def _execution_hash(plan: ExecutionPlan) -> str:
    """Deterministic hash of the execution definition only.

    Layout (CanvasLayout) is deliberately excluded: visual arrangement never
    changes the plan identity (方案 CW1 exit: layout does not change the hash).
    """
    payload = {
        "plan_key": plan.plan_key,
        "nodes": [node._hash_payload() for node in plan.nodes],
        "edges": [edge._hash_payload() for edge in plan.edges],
        "budget": {k: plan.budget[k] for k in sorted(plan.budget)},
        "parameters": plan.parameters or {},
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
