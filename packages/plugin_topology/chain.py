"""Precise invocation-chain derivation for Plugin Topology (M3).

A chain is derived deterministically from one ``plan_only`` routing plan: a
Kahn topological order over the plan DAG (tie-broken by slot_key), primary
nodes plus bounded fallback slots, and port bindings that only wire declared
contract items or the four public bridge references.  Nothing executes:
``InvocationIntent`` is a status projection and lifecycle stays materialized
unless a Policy decision moves it forward.

Determinism contract (M3 acceptance #2): the same plan plus the same locked
release yields the same chain order, the same port bindings and the same
chain checksum.  The digest formula below is also reproduced by migration
0039 for its seed so re-materialization can be asserted equal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .planner import PlannerCycleError
from .resolver import ContractResolver

# Cross-domain public references (identical to service._BRIDGE_REF_PREFIX).
_PUBLIC_BRIDGE_REF_KINDS = {"artifact_ref", "capability_contract", "released_graph_ref", "health_signal"}

_PLAN_ONLY = "plan_only"
_ISOLATION = "isolated_subprocess"
_SIDE_EFFECTS = "read_only"

INTENT_STATUSES = ("materialized", "policy_allowed", "approved_projection", "denied")
INTENT_DECISIONS = ("allowed", "requires_approval", "denied")


@dataclass(frozen=True, slots=True)
class ChainNode:
    """One primary node of the deterministic chain order."""

    slot_key: str
    blueprint_key: str
    capability: str
    ordinal: int
    role: str = "primary"


@dataclass(frozen=True, slots=True)
class FallbackEntry:
    """One declared alternative blueprint for a chain slot (never auto-substituted)."""

    slot_key: str
    blueprint_key: str
    order: int
    reason: str = "declared alternative blueprint (contract-compatible per planner)"


@dataclass(frozen=True, slots=True)
class PortBinding:
    """One contract-level wiring between a producer slot and a consumer slot.

    ``bind_mode`` is either ``contract_ref`` (declared input/output contract
    item) or ``bridge_ref`` (one of the four public cross-domain references).
    Payload transmission is deliberately disallowed so chain intent can never
    smuggle evidence bodies or domain-private data.
    """

    producer_slot: str
    consumer_slot: str
    relation_type: str
    bind_mode: str = "contract_ref"
    contract_ref: str = ""
    payload_disallowed: bool = True


@dataclass(frozen=True, slots=True)
class InvocationChain:
    """Deterministic, reproducible invocation-chain projection (M3)."""

    chain_key: str
    plan_key: str
    mode: str = _PLAN_ONLY
    planner_version: str = "1.0.0"
    chain_order: tuple[ChainNode, ...] = ()
    port_bindings: tuple[PortBinding, ...] = ()
    fallbacks: tuple[FallbackEntry, ...] = ()
    release_locked: bool = False
    checksum: str = ""
    trace_id: str = ""

    def checksum_digest(self) -> str:
        payload = {
            "chain_key": self.chain_key,
            "plan_key": self.plan_key,
            "mode": self.mode,
            "chain_order": [
                {
                    "slot_key": node.slot_key,
                    "blueprint_key": node.blueprint_key,
                    "capability": node.capability,
                    "ordinal": node.ordinal,
                    "role": node.role,
                }
                for node in self.chain_order
            ],
            "fallbacks": [
                {
                    "slot_key": entry.slot_key,
                    "blueprint_key": entry.blueprint_key,
                    "order": entry.order,
                    "reason": entry.reason,
                }
                for entry in self.fallbacks
            ],
            "port_bindings": [
                {
                    "producer_slot": binding.producer_slot,
                    "consumer_slot": binding.consumer_slot,
                    "relation_type": binding.relation_type,
                    "bind_mode": binding.bind_mode,
                    "contract_ref": binding.contract_ref,
                    "payload_disallowed": binding.payload_disallowed,
                }
                for binding in self.port_bindings
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def to_json(self) -> dict[str, Any]:
        """Schema-valid ``invocation-chain`` projection used for persisted rows."""
        return {
            "kind": "invocation_chain",
            "chain_key": self.chain_key,
            "plan_key": self.plan_key,
            "mode": self.mode,
            "planner_version": self.planner_version,
            "chain_order": [
                {
                    "slot_key": node.slot_key,
                    "blueprint_key": node.blueprint_key,
                    "capability": node.capability,
                    "ordinal": node.ordinal,
                    "role": node.role,
                }
                for node in self.chain_order
            ],
            "fallbacks": [
                {
                    "slot_key": entry.slot_key,
                    "blueprint_key": entry.blueprint_key,
                    "order": entry.order,
                    "reason": entry.reason,
                }
                for entry in self.fallbacks
            ],
            "port_bindings": [
                {
                    "producer_slot": binding.producer_slot,
                    "consumer_slot": binding.consumer_slot,
                    "relation_type": binding.relation_type,
                    "bind_mode": binding.bind_mode,
                    "contract_ref": binding.contract_ref,
                    "payload_disallowed": binding.payload_disallowed,
                }
                for binding in self.port_bindings
            ],
            "checksum": self.checksum,
            "release_locked": self.release_locked,
        }


class ChainResolver:
    """Derive a deterministic invocation chain from a persisted plan."""

    def materialize(
        self,
        *,
        plan_key: str,
        plan_nodes: list[dict[str, Any]],
        plan_edges: list[dict[str, str]],
        blueprints: dict[str, dict[str, Any]],
        bridges: dict[str, dict[str, str]],
        planner_version: str = "1.0.0",
        release_locked: bool = False,
        trace_id: str = "",
    ) -> InvocationChain:
        """Build the chain; every step is deterministic for a locked plan.

        ``plan_nodes`` carry ``slot_key``/``blueprint_key``/``capability`` and
        ``alternatives``; ``plan_edges`` carry blueprint-key endpoints and a
        ``relation_type``; ``bridges`` maps a producer blueprint key to
        ``{ref_kind, bridge_ref}`` for bridge-typed edges.
        """
        slot_of: dict[str, str] = {}
        capability_of: dict[str, str] = {}
        alternatives_of: dict[str, list[str]] = {}
        for node in plan_nodes:
            slot_key = str(node["slot_key"])
            blueprint_key = str(node["blueprint_key"])
            slot_of[blueprint_key] = slot_key
            capability_of[slot_key] = str(node.get("capability") or "")
            alternatives_of[slot_key] = [str(item) for item in node.get("alternatives") or ()]

        adjacency: dict[str, list[tuple[str, str]]] = {}
        for edge in plan_edges:
            source = str(edge["source_node"])
            target = str(edge["target_node"])
            relation = str(edge.get("relation_type") or "depends_on")
            adjacency.setdefault(source, []).append((target, relation))

        order = self._topological_order(slot_of, adjacency)
        chain_nodes = tuple(
            ChainNode(
                slot_key=slot_of[key],
                blueprint_key=key,
                capability=capability_of.get(slot_of[key], ""),
                ordinal=index,
            )
            for index, key in enumerate(order)
        )

        bindings = self._port_bindings(adjacency, slot_of, blueprints, bridges)
        fallbacks = tuple(
            FallbackEntry(slot_key=slot_key, blueprint_key=alternative, order=order_index)
            for slot_key, alternatives in alternatives_of.items()
            for order_index, alternative in enumerate(alternatives[:8])
        )

        chain_key = "chain-" + hashlib.sha256(
            "|".join([plan_key, *[node.blueprint_key for node in chain_nodes]]).encode("utf-8")
        ).hexdigest()[:16]
        chain = InvocationChain(
            chain_key=chain_key,
            plan_key=plan_key,
            mode=_PLAN_ONLY,
            planner_version=planner_version,
            chain_order=chain_nodes,
            port_bindings=bindings,
            fallbacks=fallbacks,
            release_locked=release_locked,
            trace_id=trace_id,
        )
        return InvocationChain(
            chain_key=chain_key,
            plan_key=plan_key,
            mode=_PLAN_ONLY,
            planner_version=planner_version,
            chain_order=chain_nodes,
            port_bindings=bindings,
            fallbacks=fallbacks,
            release_locked=release_locked,
            checksum=chain.checksum_digest(),
            trace_id=trace_id,
        )

    @staticmethod
    def _topological_order(
        slot_of: dict[str, str], adjacency: dict[str, list[tuple[str, str]]]
    ) -> list[str]:
        """Kahn's algorithm over chosen blueprint keys; ties break by slot_key."""
        chosen = list(slot_of.keys())
        indegree = {key: 0 for key in chosen}
        for source, targets in adjacency.items():
            if source not in indegree:
                continue
            for target, _ in targets:
                if target in indegree:
                    indegree[target] += 1
        ready = [key for key in chosen if indegree[key] == 0]
        ordered: list[str] = []
        while ready:
            ready.sort(key=lambda key: slot_of[key])
            current = ready.pop(0)
            ordered.append(current)
            for target, _ in adjacency.get(current, ()):
                if target not in indegree:
                    continue
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if len(ordered) != len(chosen):
            raise PlannerCycleError("plan DAG contains a cycle; invocation chain requires a total order")
        return ordered

    def _port_bindings(
        self,
        adjacency: dict[str, list[tuple[str, str]]],
        slot_of: dict[str, str],
        blueprints: dict[str, dict[str, Any]],
        bridges: dict[str, dict[str, str]],
    ) -> tuple[PortBinding, ...]:
        bindings: list[PortBinding] = []
        for source, targets in adjacency.items():
            if source not in slot_of:
                continue
            producer_contract = blueprints.get(source) or {}
            for target, relation in targets:
                if target not in slot_of:
                    continue
                if relation == "bridge":
                    bindings.append(self._bridge_binding(source, slot_of, target, bridges))
                else:
                    bindings.append(self._contract_binding(source, slot_of, target, producer_contract, blueprints, relation))
        bindings.sort(key=lambda item: (item.producer_slot, item.consumer_slot, item.relation_type))
        return tuple(bindings)

    @staticmethod
    def _bridge_binding(
        source: str,
        slot_of: dict[str, str],
        target: str,
        bridges: dict[str, dict[str, str]],
    ) -> PortBinding:
        declared = bridges.get(source)
        if not declared:
            raise ValueError(f"bridge edge requires a registered public bridge on producer: {source}")
        ref_kind = str(declared.get("ref_kind") or "")
        bridge_ref = str(declared.get("bridge_ref") or "")
        if ref_kind not in _PUBLIC_BRIDGE_REF_KINDS or not bridge_ref:
            raise ValueError(f"bridge edge references an invalid public bridge on producer: {source}")
        return PortBinding(
            producer_slot=slot_of[source],
            consumer_slot=slot_of[target],
            relation_type="bridge",
            bind_mode="bridge_ref",
            contract_ref=bridge_ref,
        )

    @staticmethod
    def _contract_binding(
        source: str,
        slot_of: dict[str, str],
        target: str,
        producer_contract: dict[str, Any],
        blueprints: dict[str, dict[str, Any]],
        relation: str,
    ) -> PortBinding:
        consumer_contract = blueprints.get(target) or {}
        producer_outputs = {str(item) for item in producer_contract.get("outputs") or []}
        consumer_inputs = {str(item) for item in consumer_contract.get("inputs") or []}
        shared = sorted(consumer_inputs & producer_outputs)
        if consumer_inputs and not shared:
            raise ValueError(
                f"no shared contract item between {source}->{target}: "
                f"inputs {sorted(consumer_inputs)} vs outputs {sorted(producer_outputs)}"
            )
        if not ContractResolver.version_compatible(
            str(producer_contract.get("version") or ""), consumer_contract.get("version")
        ):
            raise ValueError(
                f"contract version mismatch on {source}->{target}: producer "
                f"'{producer_contract.get('version')}' vs consumer '{consumer_contract.get('version')}'"
            )
        contract_ref = shared[0] if shared else str(producer_contract.get("capability") or "")
        if not contract_ref:
            raise ValueError(f"cannot resolve contract reference on edge {source}->{target}")
        return PortBinding(
            producer_slot=slot_of[source],
            consumer_slot=slot_of[target],
            relation_type=relation,
            bind_mode="contract_ref",
            contract_ref=contract_ref,
        )


@dataclass(frozen=True, slots=True)
class InvocationIntent:
    """One immutable intent projection for a chain node (never executed here)."""

    slot_key: str
    capability: str
    expected_inputs: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    role: str = "primary"
    policy_decision: str = "allowed"
    status: str = "policy_allowed"
    approval_ref: str = ""
    trace_id: str = ""

    def to_json(self) -> dict[str, Any]:
        if self.role not in {"primary", "fallback"}:
            raise ValueError(f"invalid intent role: {self.role}")
        if self.policy_decision not in INTENT_DECISIONS:
            raise ValueError(f"invalid policy decision: {self.policy_decision}")
        if self.status not in INTENT_STATUSES:
            raise ValueError(f"invalid intent status: {self.status}")
        return {
            "kind": "invocation_intent",
            "slot_key": self.slot_key,
            "role": self.role,
            "capability": self.capability,
            "expected_inputs": list(self.expected_inputs),
            "expected_outputs": list(self.expected_outputs),
            "isolation": _ISOLATION,
            "side_effects": _SIDE_EFFECTS,
            "policy_decision": self.policy_decision,
            "approval_ref": self.approval_ref or None,
            "status": self.status,
        }


class IntentGenerator:
    """Project one intent per primary chain node with contract-bound inputs."""

    def __init__(self, *, default_decision: str = "allowed", default_status: str = "policy_allowed") -> None:
        self.default_decision = default_decision
        self.default_status = default_status

    def intents(
        self,
        chain: InvocationChain,
        blueprints: dict[str, dict[str, Any]],
        *,
        trace_id: str = "",
        decisions: dict[str, tuple[str, str]] | None = None,
    ) -> tuple[InvocationIntent, ...]:
        """Deterministic intent list in chain order.

        ``decisions`` optionally overrides ``(policy_decision, status)`` per
        slot_key; the rest default to ``allowed``/``policy_allowed`` because
        every M3 projection is a read-only simulation.
        """
        decisions = decisions or {}
        inputs_by_consumer: dict[str, list[str]] = {}
        for binding in chain.port_bindings:
            inputs_by_consumer.setdefault(binding.consumer_slot, []).append(binding.contract_ref)
        outputs_by_slot: dict[str, list[str]] = {}
        for node in chain.chain_order:
            contract = blueprints.get(node.blueprint_key) or {}
            outputs_by_slot[node.slot_key] = [str(item) for item in contract.get("outputs") or ()]
        intents: list[InvocationIntent] = []
        for node in chain.chain_order:
            decision, status = decisions.get(node.slot_key, (self.default_decision, self.default_status))
            intents.append(
                InvocationIntent(
                    slot_key=node.slot_key,
                    capability=node.capability,
                    expected_inputs=tuple(sorted(inputs_by_consumer.get(node.slot_key, ()))),
                    expected_outputs=tuple(outputs_by_slot.get(node.slot_key, ())),
                    role="primary",
                    policy_decision=decision,
                    status=status,
                    trace_id=trace_id,
                )
            )
        return tuple(intents)