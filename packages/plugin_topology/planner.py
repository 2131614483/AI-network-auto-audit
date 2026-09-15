"""Plan-only routing planner for Plugin Topology (M1).

The planner is deliberately pure: it takes catalog snapshots and a budget and
returns a routing plan.  It never executes, never reads host state and never
opens an execution queue.  ``mode`` is always ``plan_only``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .resolver import ContractResolver


class PlannerCycleError(ValueError):
    """Raised when topology edges form a cycle that must not be planned."""


@dataclass(frozen=True, slots=True)
class RoutingPlanNode:
    """One chosen slot in a plan: the winner plus bounded alternatives."""

    slot_key: str
    capability: str
    blueprint_key: str
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutingPlanEdge:
    source_node: str
    target_node: str
    relation_type: str


@dataclass(frozen=True, slots=True)
class RoutingPlan:
    """Deterministic, reproducible plan_only result."""

    plan_key: str
    mission_key: str
    mode: str = "plan_only"
    nodes: tuple[RoutingPlanNode, ...] = ()
    edges: tuple[RoutingPlanEdge, ...] = ()
    checksum: str = ""
    locked_release: dict[str, str] | None = None
    planner_version: str = "1.0.0"
    trace_id: str = ""

    def checksum_digest(self) -> str:
        payload = {
            "nodes": [
                {
                    "slot_key": node.slot_key,
                    "capability": node.capability,
                    "blueprint_key": node.blueprint_key,
                    "alternatives": list(node.alternatives),
                }
                for node in self.nodes
            ],
            "edges": [
                {
                    "source_node": edge.source_node,
                    "target_node": edge.target_node,
                    "relation_type": edge.relation_type,
                }
                for edge in self.edges
            ],
            "mode": self.mode,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class TopologyPlanner:
    """Assemble acyclic candidate chains under budget constraints.

    Convergence is multi-axis: a candidate blueprint must be a member of the
    business domain, governance zone, capability family and runtime pool that
    the requirement was narrowed to.  Full-catalog traversal is forbidden.
    """

    def __init__(self) -> None:
        self.resolver = ContractResolver()

    def plan(
        self,
        *,
        capability_requirements: list[str],
        budget: dict[str, int],
        blueprints: dict[str, dict[str, Any]],
        cluster_memberships: dict[str, list[str]],
        edges: list[dict[str, str]],
        locked_release: dict[str, str] | None = None,
        planner_version: str = "1.0.0",
        max_candidates: int = 100,
    ) -> RoutingPlan:
        """Build a plan_only chain for the given requirements and catalog.

        ``blueprints`` maps blueprint_key -> capability contract snapshot
        (``capability``/``version``/``outputs``/``inputs`` plus ``primary_cluster_key``).
        ``cluster_memberships`` maps blueprint_key -> list of cluster keys on
        every axis the requirement narrowed to.
        ``edges`` is a list of ``source_blueprint_key``/``target_blueprint_key``/
        ``relation_type`` entries that must remain acyclic.
        """
        max_chain = int(budget.get("max_chain_length", 8))
        max_budget_candidates = int(budget.get("max_candidates", 8))
        limit = max(1, min(max_candidates, max_budget_candidates))

        selected: list[dict[str, Any]] = []
        for requirement in capability_requirements:
            matched: list[dict[str, Any]] = []
            for blueprint_key, contract in blueprints.items():
                memberships = cluster_memberships.get(blueprint_key, [])
                # Multi-axis convergence: every blueprint must belong to a
                # cluster on at least one axis; candidates that roam outside
                # all narrowed clusters are not reachable.
                if not memberships:
                    continue
                if not ContractResolver.capability_matches(requirement, str(contract.get("capability") or "")):
                    continue
                matched.append({"key": blueprint_key, "contract": contract, "memberships": memberships})
            if not matched:
                raise ValueError(f"no planned blueprint satisfies capability requirement: {requirement}")
            matched.sort(key=lambda item: item["key"])
            chosen = matched[0]
            alternatives = tuple(item["key"] for item in matched[1:limit])
            selected.append(
                {
                    "slot_key": requirement,
                    "capability": str(chosen["contract"].get("capability") or requirement),
                    "blueprint_key": chosen["key"],
                    "alternatives": alternatives,
                }
            )
            if len(selected) >= max_chain:
                break

        # Assemble DAG from declared topology edges; reject cycles outright.
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for edge in edges:
            source = str(edge.get("source_blueprint_key") or "")
            target = str(edge.get("target_blueprint_key") or "")
            relation = str(edge.get("relation_type") or "depends_on")
            adjacency.setdefault(source, []).append((target, relation))

        plan_edges: list[RoutingPlanEdge] = []
        chosen_keys = [item["blueprint_key"] for item in selected]
        chosen_set = set(chosen_keys)
        for item in selected:
            source = item["blueprint_key"]
            for target, relation in adjacency.get(source, []):
                if target not in chosen_set or target == source:
                    continue
                plan_edges.append(RoutingPlanEdge(source_node=source, target_node=target, relation_type=relation))

        self._assert_acyclic(chosen_keys, plan_edges)

        nodes = tuple(
            RoutingPlanNode(
                slot_key=item["slot_key"],
                capability=item["capability"],
                blueprint_key=item["blueprint_key"],
                alternatives=item["alternatives"],
            )
            for item in selected
        )
        plan_key = "plan-" + hashlib.sha256(
            "|".join(chosen_keys).encode("utf-8")
        ).hexdigest()[:16]
        plan = RoutingPlan(
            plan_key=plan_key,
            mission_key="|".join(capability_requirements),
            mode="plan_only",
            nodes=nodes,
            edges=tuple(plan_edges),
            locked_release=locked_release,
            planner_version=planner_version,
        )
        return RoutingPlan(
            plan_key=plan_key,
            mission_key=plan.mission_key,
            mode="plan_only",
            nodes=nodes,
            edges=tuple(plan_edges),
            checksum=plan.checksum_digest(),
            locked_release=locked_release,
            planner_version=planner_version,
        )

    @staticmethod
    def _assert_acyclic(chosen_keys: list[str], plan_edges: list[RoutingPlanEdge]) -> None:
        """DFS cycle detection; a single-pass plan must never contain a loop."""
        adjacency: dict[str, list[str]] = {key: [] for key in chosen_keys}
        for edge in plan_edges:
            adjacency.setdefault(edge.source_node, []).append(edge.target_node)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise PlannerCycleError(f"topology edges form a cycle through '{key}'")
            if key in visited:
                return
            visiting.add(key)
            for target in adjacency.get(key, []):
                if target in chosen_keys:
                    visit(target)
            visiting.remove(key)
            visited.add(key)

        for key in chosen_keys:
            visit(key)
