from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import Json, register_uuid

register_uuid()  # type: ignore[no-untyped-call]

_NODE_KEY_RE = re.compile(r"^[a-z][a-z0-9._-]*:[^\s:]+$")
_NODE_KEY_MAX = 512


def _is_namespaced_key(value: str) -> bool:
    return bool(value) and len(value) <= _NODE_KEY_MAX and bool(_NODE_KEY_RE.match(value))


def _checksum(*parts: str) -> str:
    canonical = "|".join(sorted(parts))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GraphBudget:
    max_graphs: int = 1
    max_hops: int = 2
    max_frontier: int = 50
    max_nodes: int = 100
    max_edges: int = 200
    max_bridge_hops: int = 0
    max_latency_ms: int = 3_000
    min_confidence: float = 0.0
    allowed_relation_types: tuple[str, ...] | None = None
    allowed_graph_space_keys: tuple[str, ...] | None = None

    def validate(self) -> None:
        if not 1 <= self.max_graphs <= 100:
            raise ValueError("max_graphs must be between 1 and 100")
        if not 0 <= self.max_hops <= 20:
            raise ValueError("max_hops must be between 0 and 20")
        if not 1 <= self.max_frontier <= 10_000:
            raise ValueError("max_frontier must be between 1 and 10000")
        if not 1 <= self.max_nodes <= 100_000:
            raise ValueError("max_nodes must be between 1 and 100000")
        if not 0 <= self.max_edges <= 500_000:
            raise ValueError("max_edges must be between 0 and 500000")
        if not 0 <= self.max_bridge_hops <= 5:
            raise ValueError("max_bridge_hops must be between 0 and 5")
        if not 1 <= self.max_latency_ms <= 120_000:
            raise ValueError("max_latency_ms must be between 1 and 120000")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if self.allowed_relation_types is not None and any(not item for item in self.allowed_relation_types):
            raise ValueError("allowed_relation_types cannot contain an empty relation")


_ROLE_BY_LEVEL = {
    "L0": "cluster",
    "L1": "blueprint",
    "L2": "capability",
    "L3": "domain",
    "L4": "runtime_evidence",
}
_BRIDGE_RELATION_TYPES = frozenset({"artifact_ref", "capability_contract", "released_graph_ref", "health_signal"})


class GraphService:
    def __init__(self, database_url: str, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.tenant_slug = tenant_slug

    def _tenant(self, cur: Any) -> UUID:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {self.tenant_slug}")
        tenant_id: UUID = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return tenant_id

    def ensure_space(
        self,
        key: str,
        level: str,
        name: str,
        *,
        cluster_key: str | None = None,
        graph_role: str | None = None,
        description: str = "",
    ) -> UUID:
        if level not in _ROLE_BY_LEVEL:
            raise ValueError("graph level must be between L0 and L4")
        resolved_role = graph_role or _ROLE_BY_LEVEL[level]
        if resolved_role != _ROLE_BY_LEVEL[level]:
            raise ValueError(f"graph role {resolved_role!r} is not valid for {level}")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """INSERT INTO graph.spaces(tenant_id,key,level,name) VALUES(%s,%s,%s,%s)
                    ON CONFLICT(tenant_id,key) DO UPDATE SET name=EXCLUDED.name, level=EXCLUDED.level
                    RETURNING id""",
                    (tenant_id, key, level, name),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("space upsert returned no id")
                space_id = UUID(str(row[0]))
                cur.execute(
                    """INSERT INTO graph.space_profiles(space_id,tenant_id,graph_role,cluster_key,description)
                    VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT(space_id) DO UPDATE SET graph_role=EXCLUDED.graph_role,
                    cluster_key=EXCLUDED.cluster_key,description=EXCLUDED.description,updated_at=now()""",
                    (space_id, tenant_id, resolved_role, cluster_key or key, description),
                )
                return space_id

    def upsert_node(self, space_key: str, canonical_key: str, node_type: str, label: str, properties: dict[str, Any] | None = None) -> UUID:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute("SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s", (tenant_id, space_key))
                space = cur.fetchone()
                if space is None:
                    raise ValueError(f"graph space not found: {space_key}")
                cur.execute("SELECT set_config('app.graph_revision_event', 'update', true)")
                cur.execute(
                    """INSERT INTO graph.nodes(tenant_id,space_id,canonical_key,node_type,label,properties)
                    VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(space_id,canonical_key)
                    DO UPDATE SET label=EXCLUDED.label, node_type=EXCLUDED.node_type,
                    properties=EXCLUDED.properties, row_version=graph.nodes.row_version+1, deleted_at=NULL
                    RETURNING id""",
                    (tenant_id, space[0], canonical_key, node_type, label, Json(properties or {})),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("node upsert returned no id")
                return UUID(str(row[0]))

    def register_bridge(
        self,
        source_space_key: str,
        target_space_key: str,
        source_node_id: UUID,
        target_node_id: UUID,
        relation_type: str,
        weight: float = 1.0,
    ) -> UUID:
        """Register and create one directed bridge in a single transaction.

        The database trigger rechecks the rule at commit time so direct SQL
        cannot bypass this service's narrow relation allowlist.
        """
        if relation_type not in _BRIDGE_RELATION_TYPES:
            raise ValueError("bridge relation type is not registered")
        if not 0 <= weight <= 1:
            raise ValueError("bridge weight must be between 0 and 1")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    "SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s",
                    (tenant_id, source_space_key),
                )
                source_space = cur.fetchone()
                cur.execute(
                    "SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s",
                    (tenant_id, target_space_key),
                )
                target_space = cur.fetchone()
                if source_space is None or target_space is None:
                    raise ValueError("bridge graph space not found")
                if source_space[0] == target_space[0]:
                    raise ValueError("bridge must connect different graph spaces")
                cur.execute(
                    """INSERT INTO graph.bridge_rules(tenant_id,source_space_id,target_space_id,relation_type,status,max_weight)
                    VALUES(%s,%s,%s,%s,'active',1)
                    ON CONFLICT(tenant_id,source_space_id,target_space_id,relation_type)
                    DO UPDATE SET status='active',max_weight=GREATEST(graph.bridge_rules.max_weight,EXCLUDED.max_weight),updated_at=now()""",
                    (tenant_id, source_space[0], target_space[0], relation_type),
                )
                cur.execute(
                    """INSERT INTO graph.bridge_edges(
                    tenant_id,source_space_id,target_space_id,source_node_id,target_node_id,relation_type,weight,status)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,'active')
                    ON CONFLICT(source_node_id,target_node_id,relation_type)
                    DO UPDATE SET weight=EXCLUDED.weight,status='active'
                    RETURNING id""",
                    (tenant_id, source_space[0], target_space[0], source_node_id, target_node_id, relation_type, weight),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("bridge upsert returned no id")
                return UUID(str(row[0]))

    def upsert_edge(self, space_key: str, source_node_id: UUID, target_node_id: UUID, relation_type: str, weight: float = 1.0) -> UUID:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute("SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s", (tenant_id, space_key))
                space = cur.fetchone()
                if space is None:
                    raise ValueError(f"graph space not found: {space_key}")
                cur.execute(
                    """INSERT INTO graph.edges(tenant_id,space_id,source_node_id,target_node_id,relation_type,weight)
                    VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(space_id,source_node_id,target_node_id,relation_type)
                    DO UPDATE SET weight=EXCLUDED.weight, valid_to=NULL RETURNING id""",
                    (tenant_id, space[0], source_node_id, target_node_id, relation_type, weight),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("edge upsert returned no id")
                return UUID(str(row[0]))

    def bounded_neighbors(self, space_key: str, start_node_id: UUID, budget: GraphBudget | None = None) -> dict[str, Any]:
        budget = budget or GraphBudget()
        budget.validate()
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute("SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s", (tenant_id, space_key))
                space = cur.fetchone()
                if space is None:
                    raise ValueError(f"graph space not found: {space_key}")
                seen: set[UUID] = {start_node_id}
                frontier: set[UUID] = {start_node_id}
                edges: list[dict[str, Any]] = []
                seen_edges: set[tuple[UUID, UUID, str]] = set()
                partial = False
                for _ in range(budget.max_hops):
                    if not frontier or len(seen) >= budget.max_nodes or len(edges) >= budget.max_edges:
                        partial = bool(frontier)
                        break
                    placeholders = ",".join(["%s"] * len(frontier))
                    cur.execute(
                        f"""SELECT e.source_node_id,e.target_node_id,e.relation_type,e.weight FROM graph.edges e
                        JOIN graph.nodes source_node ON source_node.id=e.source_node_id
                        JOIN graph.nodes target_node ON target_node.id=e.target_node_id
                        WHERE e.tenant_id=%s AND e.space_id=%s AND e.valid_to IS NULL AND e.deleted_at IS NULL
                        AND source_node.deleted_at IS NULL AND target_node.deleted_at IS NULL
                        AND (e.source_node_id IN ({placeholders}) OR e.target_node_id IN ({placeholders}))
                        LIMIT %s""",
                        (tenant_id, space[0], *frontier, *frontier, budget.max_edges - len(edges)),
                    )
                    next_frontier: set[UUID] = set()
                    for source, target, relation, weight in cur.fetchall():
                        edge_key = (source, target, relation)
                        if edge_key in seen_edges:
                            continue
                        seen_edges.add(edge_key)
                        edges.append({"source": str(source), "target": str(target), "relation": relation, "weight": float(weight)})
                        for node_id in (source, target):
                            if node_id not in seen and len(seen) < budget.max_nodes:
                                seen.add(node_id)
                                next_frontier.add(node_id)
                    frontier = next_frontier
                if frontier:
                    partial = True
                return {"nodes": [str(node_id) for node_id in seen], "edges": edges[: budget.max_edges], "partial": partial}

    @staticmethod
    def _budget_payload(budget: GraphBudget) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "max_graphs": budget.max_graphs,
            "max_hops": budget.max_hops,
            "max_frontier": budget.max_frontier,
            "max_nodes": budget.max_nodes,
            "max_edges": budget.max_edges,
            "max_bridge_hops": budget.max_bridge_hops,
            "max_latency_ms": budget.max_latency_ms,
            "min_confidence": budget.min_confidence,
        }
        if budget.allowed_relation_types is not None:
            payload["allowed_relation_types"] = list(budget.allowed_relation_types)
        if budget.allowed_graph_space_keys is not None:
            payload["allowed_graph_space_keys"] = list(budget.allowed_graph_space_keys)
        return payload

    def bounded_route(
        self,
        space_key: str,
        start_node_id: UUID,
        budget: GraphBudget | None = None,
    ) -> dict[str, Any]:
        """Traverse local edges and registered bridges under explicit budgets.

        The traversal intentionally makes no graph-wide recursive SQL query.
        It expands only the previous bounded frontier and fetches at most one
        extra candidate per query to report an honest truncation reason.
        """
        budget = budget or GraphBudget()
        budget.validate()
        started = monotonic()
        reasons: set[str] = set()
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT s.id,s.key FROM graph.spaces s JOIN graph.nodes n ON n.space_id=s.id
                    WHERE s.tenant_id=%s AND s.key=%s AND n.id=%s AND n.tenant_id=%s AND n.deleted_at IS NULL""",
                    (tenant_id, space_key, start_node_id, tenant_id),
                )
                start = cur.fetchone()
                if start is None:
                    raise ValueError("start node does not belong to the active graph space")
                start_space_id: UUID = start[0]
                start_space_key: str = start[1]
                allowed_spaces = set(budget.allowed_graph_space_keys or ())
                if allowed_spaces and start_space_key not in allowed_spaces:
                    raise ValueError("start graph space is outside the requested allowlist")
                seen_nodes: dict[UUID, tuple[UUID, str]] = {start_node_id: (start_space_id, start_space_key)}
                visited_spaces: dict[UUID, str] = {start_space_id: start_space_key}
                frontier: list[tuple[UUID, UUID, str, int, int]] = [
                    (start_node_id, start_space_id, start_space_key, 0, 0)
                ]
                route_edges: list[dict[str, Any]] = []
                edge_keys: set[tuple[UUID, UUID, str, str]] = set()
                relation_types = tuple(budget.allowed_relation_types or ())

                def can_continue() -> bool:
                    if (monotonic() - started) * 1000 <= budget.max_latency_ms:
                        return True
                    reasons.add("max_latency_ms")
                    return False

                def append_frontier(item: tuple[UUID, UUID, str, int, int], next_frontier: list[tuple[UUID, UUID, str, int, int]]) -> None:
                    node_id, node_space_id, node_space_key, _, _ = item
                    if node_id in seen_nodes:
                        return
                    if len(seen_nodes) >= budget.max_nodes:
                        reasons.add("max_nodes")
                        return
                    if len(next_frontier) >= budget.max_frontier:
                        reasons.add("max_frontier")
                        return
                    seen_nodes[node_id] = (node_space_id, node_space_key)
                    next_frontier.append(item)

                while frontier and can_continue():
                    next_frontier: list[tuple[UUID, UUID, str, int, int]] = []
                    local_groups: dict[tuple[UUID, str], list[tuple[UUID, int, int]]] = {}
                    for node_id, node_space_id, node_space_key, local_hops, bridge_hops in frontier:
                        if local_hops >= budget.max_hops:
                            reasons.add("max_hops")
                            continue
                        local_groups.setdefault((node_space_id, node_space_key), []).append((node_id, local_hops, bridge_hops))
                    for (node_space_id, node_space_key), nodes in local_groups.items():
                        if not can_continue() or len(route_edges) >= budget.max_edges:
                            reasons.add("max_edges")
                            break
                        node_ids = [item[0] for item in nodes]
                        current = {item[0]: (item[1], item[2]) for item in nodes}
                        placeholders = ",".join(["%s"] * len(node_ids))
                        relation_clause = ""
                        params: list[Any] = [tenant_id, node_space_id, *node_ids, *node_ids, budget.min_confidence]
                        if relation_types:
                            relation_clause = " AND e.relation_type = ANY(%s)"
                            params.append(list(relation_types))
                        params.append(budget.max_edges - len(route_edges) + 1)
                        cur.execute(
                            f"""SELECT e.source_node_id,e.target_node_id,e.relation_type,e.weight
                            FROM graph.edges e JOIN graph.nodes source_node ON source_node.id=e.source_node_id
                            JOIN graph.nodes target_node ON target_node.id=e.target_node_id
                            WHERE e.tenant_id=%s AND e.space_id=%s AND e.valid_to IS NULL AND e.deleted_at IS NULL
                              AND source_node.deleted_at IS NULL AND target_node.deleted_at IS NULL
                              AND (e.source_node_id IN ({placeholders}) OR e.target_node_id IN ({placeholders}))
                              AND e.weight >= %s {relation_clause}
                            ORDER BY e.id LIMIT %s""",
                            params,
                        )
                        rows = cur.fetchall()
                        if len(rows) > budget.max_edges - len(route_edges):
                            reasons.add("max_edges")
                        for source, target, relation, weight in rows[: budget.max_edges - len(route_edges)]:
                            edge_key = (source, target, relation, "internal")
                            if edge_key in edge_keys:
                                continue
                            edge_keys.add(edge_key)
                            route_edges.append({
                                "source": str(source), "target": str(target), "relation": relation,
                                "weight": float(weight), "kind": "internal",
                                "source_space_key": node_space_key, "target_space_key": node_space_key,
                            })
                            for current_id, neighbor_id in ((source, target), (target, source)):
                                state = current.get(current_id)
                                if state is not None:
                                    append_frontier(
                                        (neighbor_id, node_space_id, node_space_key, state[0] + 1, state[1]),
                                        next_frontier,
                                    )
                    bridge_candidates = [item for item in frontier if item[4] < budget.max_bridge_hops]
                    if bridge_candidates and len(route_edges) < budget.max_edges and can_continue():
                        candidate_ids = [item[0] for item in bridge_candidates]
                        bridge_state = {item[0]: item for item in bridge_candidates}
                        placeholders = ",".join(["%s"] * len(candidate_ids))
                        relation_clause = ""
                        params = [tenant_id, *candidate_ids, budget.min_confidence]
                        if relation_types:
                            relation_clause = " AND bridge.relation_type = ANY(%s)"
                            params.append(list(relation_types))
                        params.append(budget.max_edges - len(route_edges) + 1)
                        cur.execute(
                            f"""SELECT bridge.source_node_id,bridge.target_node_id,bridge.relation_type,bridge.weight,
                            bridge.source_space_id,source_space.key,bridge.target_space_id,target_space.key
                            FROM graph.bridge_edges bridge
                            JOIN graph.bridge_rules rule ON rule.tenant_id=bridge.tenant_id
                              AND rule.source_space_id=bridge.source_space_id AND rule.target_space_id=bridge.target_space_id
                              AND rule.relation_type=bridge.relation_type AND rule.status='active'
                            JOIN graph.spaces source_space ON source_space.id=bridge.source_space_id
                            JOIN graph.spaces target_space ON target_space.id=bridge.target_space_id
                            JOIN graph.nodes target_node ON target_node.id=bridge.target_node_id
                            WHERE bridge.tenant_id=%s AND bridge.status='active' AND bridge.source_node_id IN ({placeholders})
                              AND bridge.weight >= %s AND bridge.weight <= rule.max_weight AND target_node.deleted_at IS NULL
                              {relation_clause}
                            ORDER BY bridge.id LIMIT %s""",
                            params,
                        )
                        rows = cur.fetchall()
                        if len(rows) > budget.max_edges - len(route_edges):
                            reasons.add("max_edges")
                        for source, target, relation, weight, source_space_id, source_space_key, target_space_id, target_space_key in rows[: budget.max_edges - len(route_edges)]:
                            if allowed_spaces and target_space_key not in allowed_spaces:
                                continue
                            if target_space_id not in visited_spaces and len(visited_spaces) >= budget.max_graphs:
                                reasons.add("max_graphs")
                                continue
                            edge_key = (source, target, relation, "bridge")
                            if edge_key in edge_keys:
                                continue
                            edge_keys.add(edge_key)
                            route_edges.append({
                                "source": str(source), "target": str(target), "relation": relation,
                                "weight": float(weight), "kind": "bridge",
                                "source_space_key": source_space_key, "target_space_key": target_space_key,
                            })
                            visited_spaces[target_space_id] = target_space_key
                            source_state = bridge_state.get(source)
                            if source_state is not None:
                                append_frontier(
                                    (target, target_space_id, target_space_key, source_state[3], source_state[4] + 1),
                                    next_frontier,
                                )
                    frontier = next_frontier
                return {
                    "nodes": [str(node_id) for node_id in seen_nodes],
                    "edges": route_edges,
                    "visited_space_keys": list(visited_spaces.values()),
                    "partial": bool(reasons),
                    "truncation_reasons": sorted(reasons),
                    "budget": self._budget_payload(budget),
                }

    def visualization(
        self,
        space_key: str | None = None,
        node_type: str | None = None,
        *,
        max_nodes: int = 180,
        max_edges: int = 360,
    ) -> dict[str, Any]:
        """Return a deliberately bounded, typed projection for the desktop graph canvas."""
        if not 1 <= max_nodes <= 500:
            raise ValueError("max_nodes must be between 1 and 500")
        if not 1 <= max_edges <= 1_000:
            raise ValueError("max_edges must be between 1 and 1000")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT s.id,s.key,s.level,s.name,
                    (SELECT COUNT(*) FROM graph.nodes n
                     WHERE n.tenant_id=s.tenant_id AND n.space_id=s.id AND n.deleted_at IS NULL),
                    (SELECT COUNT(*) FROM graph.edges e
                     WHERE e.tenant_id=s.tenant_id AND e.space_id=s.id
                       AND e.deleted_at IS NULL AND e.valid_to IS NULL)
                    FROM graph.spaces s
                    WHERE s.tenant_id=%s
                    ORDER BY s.name,s.key""",
                    (tenant_id,),
                )
                space_rows = cur.fetchall()
                spaces = [
                    {
                        "key": row[1], "level": row[2], "name": row[3],
                        "node_count": int(row[4]), "edge_count": int(row[5]),
                    }
                    for row in space_rows
                ]
                if not space_rows:
                    return {"space_key": None, "spaces": spaces, "nodes": [], "edges": [], "partial": False}
                matching = (
                    next((row for row in space_rows if row[1] == space_key), None)
                    if space_key
                    else next((row for row in space_rows if row[5] > 0), space_rows[0])
                )
                if matching is None:
                    raise ValueError(f"graph space not found: {space_key}")
                space_id, selected_key = matching[0], matching[1]
                if node_type:
                    cur.execute(
                        """SELECT id,label,node_type FROM graph.nodes
                        WHERE tenant_id=%s AND space_id=%s AND deleted_at IS NULL AND node_type=%s
                        ORDER BY label,id LIMIT %s""",
                        (tenant_id, space_id, node_type, max_nodes + 1),
                    )
                else:
                    cur.execute(
                        """SELECT id,label,node_type FROM graph.nodes
                        WHERE tenant_id=%s AND space_id=%s AND deleted_at IS NULL
                        ORDER BY label,id LIMIT %s""",
                        (tenant_id, space_id, max_nodes + 1),
                    )
                node_rows = cur.fetchall()
                partial = len(node_rows) > max_nodes
                visible_nodes = node_rows[:max_nodes]
                node_ids = [row[0] for row in visible_nodes]
                nodes = [{"id": str(row[0]), "label": row[1], "node_type": row[2]} for row in visible_nodes]
                if not node_ids:
                    return {"space_key": selected_key, "spaces": spaces, "nodes": nodes, "edges": [], "partial": partial}
                placeholders = ",".join(["%s"] * len(node_ids))
                cur.execute(
                    f"""SELECT source_node_id,target_node_id,relation_type,weight FROM graph.edges
                    WHERE tenant_id=%s AND space_id=%s AND deleted_at IS NULL AND valid_to IS NULL
                    AND source_node_id IN ({placeholders}) AND target_node_id IN ({placeholders})
                    ORDER BY relation_type,id LIMIT %s""",
                    (tenant_id, space_id, *node_ids, *node_ids, max_edges + 1),
                )
                edge_rows = cur.fetchall()
                partial = partial or len(edge_rows) > max_edges
                edges = [
                    {"source": str(row[0]), "target": str(row[1]), "relation": row[2], "weight": float(row[3])}
                    for row in edge_rows[:max_edges]
                ]
                return {"space_key": selected_key, "spaces": spaces, "nodes": nodes, "edges": edges, "partial": partial}

    def governance_overview(self) -> dict[str, Any]:
        """Return compact, read-only topology governance facts for the desktop."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT s.key,s.level,s.name,
                    COALESCE(profile.graph_role, CASE s.level
                      WHEN 'L0' THEN 'cluster' WHEN 'L1' THEN 'blueprint' WHEN 'L2' THEN 'capability'
                      WHEN 'L3' THEN 'domain' ELSE 'runtime_evidence' END),
                    COALESCE(profile.cluster_key,s.key),
                    COUNT(DISTINCT bridge.id) FILTER (WHERE bridge.status='active'),
                    COUNT(DISTINCT revision.id)
                    FROM graph.spaces s
                    LEFT JOIN graph.space_profiles profile ON profile.space_id=s.id AND profile.tenant_id=s.tenant_id
                    LEFT JOIN graph.bridge_edges bridge ON (bridge.source_space_id=s.id OR bridge.target_space_id=s.id)
                      AND bridge.tenant_id=s.tenant_id
                    LEFT JOIN graph.nodes node ON node.space_id=s.id AND node.tenant_id=s.tenant_id
                    LEFT JOIN graph.node_revisions revision ON revision.node_id=node.id AND revision.tenant_id=s.tenant_id
                    WHERE s.tenant_id=%s
                    GROUP BY s.id,s.key,s.level,s.name,profile.graph_role,profile.cluster_key
                    ORDER BY s.level,s.name,s.key""",
                    (tenant_id,),
                )
                spaces = [
                    {
                        "key": row[0], "level": row[1], "name": row[2], "graph_role": row[3],
                        "cluster_key": row[4], "active_bridge_count": int(row[5]), "revision_count": int(row[6]),
                    }
                    for row in cur.fetchall()
                ]
                cur.execute(
                    "SELECT COUNT(*) FROM graph.bridge_edges WHERE tenant_id=%s AND status='active'",
                    (tenant_id,),
                )
                bridge_row = cur.fetchone()
                if bridge_row is None:  # pragma: no cover - scalar query always returns one row
                    raise RuntimeError("bridge count returned no row")
                bridge_count = int(bridge_row[0])
                cur.execute(
                    "SELECT COUNT(*) FROM knowledge.conflict_cases WHERE tenant_id=%s AND status='open'",
                    (tenant_id,),
                )
                conflict_row = cur.fetchone()
                if conflict_row is None:  # pragma: no cover - scalar query always returns one row
                    raise RuntimeError("conflict count returned no row")
                open_conflicts = int(conflict_row[0])
                return {"spaces": spaces, "active_bridge_count": bridge_count, "open_conflict_count": open_conflicts}

    def soft_delete_node(self, node_id: UUID, snapshot: dict[str, Any], actor_id: UUID | None = None) -> None:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    "SELECT canonical_key,node_type,label,properties,space_id,deleted_at FROM graph.nodes WHERE tenant_id=%s AND id=%s FOR UPDATE",
                    (tenant_id, node_id),
                )
                node = cur.fetchone()
                if node is None:
                    raise ValueError("graph node not found")
                if node[5] is not None:
                    return
                stored_snapshot = {
                    "canonical_key": node[0], "node_type": node[1], "label": node[2],
                    "properties": node[3], "space_id": str(node[4]), **snapshot,
                }
                cur.execute(
                    "INSERT INTO knowledge.recycle_bin(tenant_id,entity_kind,entity_id,snapshot,deleted_by) VALUES(%s,'graph.node',%s,%s,%s)",
                    (tenant_id, node_id, Json(stored_snapshot), actor_id),
                )
                cur.execute("SELECT set_config('app.graph_revision_event', 'recycled', true)")
                cur.execute("UPDATE graph.nodes SET deleted_at=now(), row_version=row_version+1 WHERE tenant_id=%s AND id=%s", (tenant_id, node_id))

    def restore_node(self, recycle_bin_id: UUID) -> UUID:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    "SELECT entity_id FROM knowledge.recycle_bin WHERE tenant_id=%s AND id=%s AND entity_kind='graph.node' AND restored_at IS NULL",
                    (tenant_id, recycle_bin_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError("recycle-bin entry not found or already restored")
                cur.execute("SELECT set_config('app.graph_revision_event', 'restored', true)")
                cur.execute("UPDATE graph.nodes SET deleted_at=NULL, row_version=row_version+1 WHERE tenant_id=%s AND id=%s AND deleted_at IS NOT NULL", (tenant_id, row[0]))
                if cur.rowcount != 1:
                    raise ValueError("graph node not found or already active")
                cur.execute("UPDATE knowledge.recycle_bin SET restored_at=now() WHERE id=%s", (recycle_bin_id,))
                return UUID(str(row[0]))

    def list_node_revisions(self, node_id: UUID, limit: int = 30) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("revision limit must be between 1 and 100")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT id,row_version,event_type,snapshot,created_at
                    FROM graph.node_revisions WHERE tenant_id=%s AND node_id=%s
                    ORDER BY row_version DESC,id DESC LIMIT %s""",
                    (tenant_id, node_id, limit),
                )
                return [
                    {
                        "id": str(row[0]), "row_version": int(row[1]), "event_type": row[2],
                        "snapshot": row[3], "created_at": row[4],
                    }
                    for row in cur.fetchall()
                ]

    def rollback_node_revision(self, node_id: UUID, revision_id: UUID) -> UUID:
        """Restore an immutable prior node snapshot without deleting history."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT snapshot FROM graph.node_revisions
                    WHERE tenant_id=%s AND id=%s AND node_id=%s FOR SHARE""",
                    (tenant_id, revision_id, node_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError("node revision not found")
                snapshot: dict[str, Any] = row[0]
                required = {"canonical_key", "node_type", "label", "properties", "valid_from", "valid_to", "deleted_at"}
                if not required.issubset(snapshot):
                    raise ValueError("node revision snapshot is incomplete")
                cur.execute("SELECT set_config('app.graph_revision_event', 'rollback', true)")
                cur.execute(
                    """UPDATE graph.nodes SET canonical_key=%s,node_type=%s,label=%s,properties=%s,
                    valid_from=%s,valid_to=%s,deleted_at=%s,row_version=row_version+1
                    WHERE tenant_id=%s AND id=%s""",
                    (
                        snapshot["canonical_key"], snapshot["node_type"], snapshot["label"], Json(snapshot["properties"]),
                        snapshot["valid_from"], snapshot["valid_to"], snapshot["deleted_at"], tenant_id, node_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise ValueError("graph node not found")
                return node_id

    def list_conflicts(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("conflict limit must be between 1 and 500")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT case_row.id,case_row.entity_key,case_row.status,case_row.severity,case_row.summary,
                    case_row.created_at,case_row.resolved_at,COUNT(item.id)
                    FROM knowledge.conflict_cases case_row
                    LEFT JOIN knowledge.conflict_items item ON item.case_id=case_row.id AND item.tenant_id=case_row.tenant_id
                    WHERE case_row.tenant_id=%s
                    GROUP BY case_row.id,case_row.entity_key,case_row.status,case_row.severity,case_row.summary,
                    case_row.created_at,case_row.resolved_at
                    ORDER BY CASE case_row.status WHEN 'open' THEN 0 ELSE 1 END,case_row.created_at DESC
                    LIMIT %s""",
                    (tenant_id, limit),
                )
                return [
                    {
                        "id": str(row[0]), "entity_key": row[1], "status": row[2], "severity": row[3],
                        "summary": row[4], "created_at": row[5], "resolved_at": row[6], "item_count": int(row[7]),
                    }
                    for row in cur.fetchall()
                ]

    def add_alias(self, node_id: UUID, alias: str, alias_type: str = "name", confidence: float = 1.0) -> UUID:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
                    VALUES(%s,%s,%s,%s,%s) ON CONFLICT(node_id,alias) DO UPDATE SET confidence=EXCLUDED.confidence RETURNING id""",
                    (tenant_id, node_id, alias, alias_type, confidence),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("alias upsert returned no id")
                return UUID(str(row[0]))

    def record_conflict(self, entity_key: str, summary: str, source_uri: str, claim: dict[str, Any], severity: str = "medium") -> UUID:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    "INSERT INTO knowledge.conflict_cases(tenant_id,entity_key,severity,summary) VALUES(%s,%s,%s,%s) RETURNING id",
                    (tenant_id, entity_key, severity, summary),
                )
                case_row = cur.fetchone()
                if case_row is None:
                    raise RuntimeError("conflict case insert returned no id")
                cur.execute(
                    "INSERT INTO knowledge.conflict_items(tenant_id,case_id,source_uri,claim) VALUES(%s,%s,%s,%s)",
                    (tenant_id, case_row[0], source_uri, Json(claim)),
                )
                return UUID(str(case_row[0]))

    # ------------------------------------------------------------------ merge ---
    def merge_nodes(
        self,
        space_key: str,
        target_key: str,
        source_keys: list[str],
        *,
        expected_revision: int,
        reason: str,
    ) -> dict[str, Any]:
        """Governed single-space node merge.

        Re-points every edge touching a source node onto a surviving target node
        (deduplicated by the edge unique key), soft-deletes the sources into the
        recycle bin with a ``merged`` revision, folds source aliases into the
        target, and records a reversible ledger.  Optimistic lock: every node must
        match ``expected_revision`` or the merge is refused.
        """
        if not _is_namespaced_key(target_key):
            raise ValueError("target_key must be a namespaced 'domain:key'")
        if not source_keys or any(not _is_namespaced_key(k) for k in source_keys):
            raise ValueError("source_keys must be non-empty namespaced node keys")
        if target_key in source_keys:
            raise ValueError("cannot merge a node into itself")
        if expected_revision < 1:
            raise ValueError("expected_revision must be >= 1")

        lock_error: str | None = None
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                space = self._resolve_space(cur, tenant_id, space_key)
                target = self._lock_node(cur, tenant_id, space, target_key)
                sources: list[dict[str, Any]] = []
                if target["row_version"] != expected_revision:
                    self._concurrency_conflict(cur, tenant_id, space_key, target_key, expected_revision, target["row_version"], "merge")
                    lock_error = f"target {target_key} has row_version {target['row_version']}, expected {expected_revision}"
                else:
                    for key in source_keys:
                        node = self._lock_node(cur, tenant_id, space, key)
                        if node["row_version"] != expected_revision:
                            self._concurrency_conflict(cur, tenant_id, space_key, key, expected_revision, node["row_version"], "merge")
                            lock_error = f"source {key} has row_version {node['row_version']}, expected {expected_revision}"
                            break
                        sources.append(node)
                if lock_error is not None:
                    continue_status = {
                        "merge_id": "",
                        "space_key": space_key,
                        "target_key": target_key,
                        "source_keys": list(source_keys),
                        "redirected_edges": 0,
                        "status": "conflict",
                        "reason": lock_error,
                    }
                else:
                    cur.execute(
                        """INSERT INTO graph.merge_records(tenant_id,space_id,target_node_id,status,reason,checksum)
                        VALUES(%s,%s,%s,'applied',%s,%s) RETURNING id""",
                        (tenant_id, space, target["id"], reason, _checksum(target_key, *source_keys)),
                    )
                    merge_row = cur.fetchone()
                    if merge_row is None:  # pragma: no cover - insert returns a row
                        raise RuntimeError("merge record insert returned no id")
                    merge_id = merge_row[0]
                    cur.execute(
                        """INSERT INTO graph.merge_members(tenant_id,merge_id,node_id,role,node_type,label,properties,deleted_at)
                        VALUES(%s,%s,%s,'target',%s,%s,%s,NULL)""",
                        (tenant_id, merge_id, target["id"], target["node_type"], target["label"], Json(target["properties"])),
                    )
                    for src in sources:
                        cur.execute(
                            """INSERT INTO graph.merge_members(tenant_id,merge_id,node_id,role,node_type,label,properties,deleted_at)
                            VALUES(%s,%s,%s,'source',%s,%s,%s,NULL)""",
                            (tenant_id, merge_id, src["id"], src["node_type"], src["label"], Json(src["properties"])),
                        )
                    source_ids = [src["id"] for src in sources]
                    self._repoint_edges(cur, tenant_id, space, merge_id, source_ids, target["id"])
                    for src in sources:
                        self._fold_source(cur, tenant_id, space, src, target, merge_id)
                    edge_count = self._count_redirects(cur, tenant_id, merge_id)
                    continue_status = {
                        "merge_id": str(merge_id),
                        "space_key": space_key,
                        "target_key": target_key,
                        "source_keys": list(source_keys),
                        "redirected_edges": edge_count,
                        "status": "applied",
                        "reason": None,
                    }
        if lock_error is not None:
            raise ValueError(lock_error)
        return continue_status

    # ------------------------------------------------------------------ split ---
    def split_node(
        self,
        space_key: str,
        source_key: str,
        parts: list[dict[str, Any]],
        *,
        expected_revision: int,
        reason: str,
    ) -> dict[str, Any]:
        """Governed single-space node split.

        Creates one new node per part and re-points the source node's outgoing
        edges whose relation is listed in a part's ``redirect_relations`` to that
        part's node.  The source node remains active.  Ledger preserved in
        ``split_records`` / ``split_parts`` / ``split_edge_redirects``.
        """
        if not _is_namespaced_key(source_key):
            raise ValueError("source_key must be a namespaced 'domain:key'")
        if not parts:
            raise ValueError("split requires at least one part")
        if expected_revision < 1:
            raise ValueError("expected_revision must be >= 1")
        part_keys: list[str] = []
        for part in parts:
            key = part.get("node_key")
            if not isinstance(key, str) or not _is_namespaced_key(key):
                raise ValueError(f"part node_key must be a namespaced 'domain:key': {key}")
            if key == source_key:
                raise ValueError("part node_key cannot equal the source node")
            if key in part_keys:
                raise ValueError(f"duplicate part node_key: {key}")
            part_keys.append(key)

        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                space = self._resolve_space(cur, tenant_id, space_key)
                source = self._lock_node(cur, tenant_id, space, source_key)
                if source["row_version"] != expected_revision:
                    self._concurrency_conflict(cur, tenant_id, space_key, source_key, expected_revision, source["row_version"], "split")
                    raise ValueError(f"source {source_key} has row_version {source['row_version']}, expected {expected_revision}")
                for key in part_keys:
                    if self._node_exists(cur, tenant_id, space, key):
                        raise ValueError(f"part node_key already exists: {key}")
                cur.execute(
                    """INSERT INTO graph.split_records(tenant_id,space_id,source_node_id,status,reason,checksum)
                    VALUES(%s,%s,%s,'applied',%s,%s) RETURNING id""",
                    (tenant_id, space, source["id"], reason, _checksum(source_key, *part_keys)),
                )
                split_row = cur.fetchone()
                if split_row is None:  # pragma: no cover - insert returns a row
                    raise RuntimeError("split record insert returned no id")
                split_id = split_row[0]
                relation_owner: dict[str, dict[str, Any]] = {}
                for part in parts:
                    node_id = uuid4()
                    cur.execute(
                        """INSERT INTO graph.nodes(id,tenant_id,space_id,canonical_key,node_type,label,properties)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                        (node_id, tenant_id, space, part["node_key"], part["node_type"], part["label"], Json(part.get("properties") or {})),
                    )
                    cur.execute(
                        """INSERT INTO graph.split_parts(tenant_id,split_id,node_id,node_type,label,properties)
                        VALUES(%s,%s,%s,%s,%s,%s)""",
                        (tenant_id, split_id, node_id, part["node_type"], part["label"], Json(part.get("properties") or {})),
                    )
                    for rel in part.get("redirect_relations") or []:
                        relation_owner[rel] = {"node_id": node_id, "part_key": part["node_key"]}
                for rel, owner in relation_owner.items():
                    cur.execute(
                        """UPDATE graph.edges SET source_node_id=%s
                        WHERE tenant_id=%s AND space_id=%s AND source_node_id=%s AND relation_type=%s
                        RETURNING id""",
                        (owner["node_id"], tenant_id, space, source["id"], rel),
                    )
                    for (edge_id,) in cur.fetchall():
                        cur.execute(
                            """INSERT INTO graph.split_edge_redirects(tenant_id,split_id,edge_id,to_node_id,relation_type)
                            VALUES(%s,%s,%s,%s,%s)""",
                            (tenant_id, split_id, edge_id, owner["node_id"], rel),
                        )
                return {
                    "split_id": str(split_id),
                    "space_key": space_key,
                    "source_key": source_key,
                    "parts": part_keys,
                    "status": "applied",
                }

    # ------------------------------------------------------------ arbitration ---
    def arbitrate_conflict(
        self,
        case_id: UUID,
        decision: str,
        reason: str,
        *,
        merge: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Explicitly resolve an open conflict; a case is never arbitrated twice.

        decision in {merge, keep_source, reject_claim, resolve}.  A ``merge``
        decision optionally executes a governed merge and records the resolution
        ChangeSet/release id for full traceability.
        """
        if decision not in {"merge", "keep_source", "reject_claim", "resolve"}:
            raise ValueError(f"unsupported arbitration decision: {decision}")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    "SELECT status FROM knowledge.conflict_cases WHERE tenant_id=%s AND id=%s FOR UPDATE",
                    (tenant_id, case_id),
                )
                case = cur.fetchone()
                if case is None:
                    raise ValueError("conflict case not found")
                if case[0] != "open":
                    raise ValueError(f"conflict case is not open (status={case[0]})")
                cur.execute(
                    "SELECT 1 FROM knowledge.arbitration_decisions WHERE tenant_id=%s AND case_id=%s",
                    (tenant_id, case_id),
                )
                if cur.fetchone() is not None:
                    raise ValueError("conflict case already arbitrated")
                resolution_id: str | None = None
                checksum = _checksum(str(case_id), decision)
                if decision == "merge":
                    if merge is None:
                        raise ValueError("merge decision requires merge context")
                    result = self.merge_nodes(
                        merge["space_key"],
                        merge["target_key"],
                        list(merge["source_keys"]),
                        expected_revision=int(merge["expected_revision"]),
                        reason=f"arbitration of {case_id}",
                    )
                    resolution_id = result["merge_id"]
                cur.execute(
                    """INSERT INTO knowledge.arbitration_decisions
                    (tenant_id,case_id,decision,reason,resolution_changeset_id)
                    VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                    (tenant_id, case_id, decision, reason, resolution_id),
                )
                decision_row = cur.fetchone()
                if decision_row is None:  # pragma: no cover - insert returns a row
                    raise RuntimeError("arbitration decision insert returned no id")
                decision_id = decision_row[0]
                cur.execute(
                    "UPDATE knowledge.conflict_cases SET status='resolved', resolved_at=now() WHERE tenant_id=%s AND id=%s",
                    (tenant_id, case_id),
                )
                return {
                    "decision_id": str(decision_id),
                    "case_id": str(case_id),
                    "decision": decision,
                    "status": "resolved",
                    "resolution_merge_id": resolution_id or checksum if decision == "merge" else None,
                    "resolved": True,
                }

    # ------------------------------------------------------------------ reads ---
    def list_merge_records(self, space_key: str, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                space = self._resolve_space(cur, tenant_id, space_key)
                cur.execute(
                    """SELECT m.id,m.target_node_id,t.canonical_key,m.status,m.reason,m.checksum,m.created_at,
                    (SELECT count(*) FROM graph.merge_members mm WHERE mm.merge_id=m.id AND mm.role='source'),
                    (SELECT count(*) FROM graph.merge_edge_redirects r WHERE r.merge_id=m.id)
                    FROM graph.merge_records m JOIN graph.nodes t ON t.id=m.target_node_id
                    WHERE m.tenant_id=%s AND m.space_id=%s ORDER BY m.created_at DESC LIMIT %s""",
                    (tenant_id, space, limit),
                )
                return [
                    {
                        "id": str(row[0]), "target_node_key": row[2], "status": row[3], "reason": row[4],
                        "checksum": row[5], "created_at": row[6], "source_count": int(row[7]),
                        "redirected_edges": int(row[8]),
                    }
                    for row in cur.fetchall()
                ]

    def list_split_records(self, space_key: str, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                space = self._resolve_space(cur, tenant_id, space_key)
                cur.execute(
                    """SELECT sp.id,sp.source_node_id,s.canonical_key,sp.status,sp.reason,sp.checksum,sp.created_at,
                    (SELECT count(*) FROM graph.split_parts p WHERE p.split_id=sp.id),
                    (SELECT count(*) FROM graph.split_edge_redirects r WHERE r.split_id=sp.id)
                    FROM graph.split_records sp JOIN graph.nodes s ON s.id=sp.source_node_id
                    WHERE sp.tenant_id=%s AND sp.space_id=%s ORDER BY sp.created_at DESC LIMIT %s""",
                    (tenant_id, space, limit),
                )
                return [
                    {
                        "id": str(row[0]), "source_node_key": row[2], "status": row[3], "reason": row[4],
                        "checksum": row[5], "created_at": row[6], "part_count": int(row[7]),
                        "redirected_edges": int(row[8]),
                    }
                    for row in cur.fetchall()
                ]

    # ---------------------------------------------------------------- helpers ---
    def _resolve_space(self, cur: Any, tenant_id: UUID, space_key: str) -> UUID:
        cur.execute("SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s", (tenant_id, space_key))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"graph space not found: {space_key}")
        return UUID(str(row[0]))

    def _lock_node(self, cur: Any, tenant_id: UUID, space_id: UUID, canonical_key: str) -> dict[str, Any]:
        cur.execute(
            """SELECT id,row_version,node_type,label,properties,canonical_key FROM graph.nodes
            WHERE tenant_id=%s AND space_id=%s AND canonical_key=%s AND deleted_at IS NULL FOR UPDATE""",
            (tenant_id, space_id, canonical_key),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"active graph node not found: {canonical_key}")
        return {
            "id": row[0], "row_version": int(row[1]), "node_type": row[2], "label": row[3],
            "properties": row[4], "canonical_key": row[5],
        }

    def _node_exists(self, cur: Any, tenant_id: UUID, space_id: UUID, canonical_key: str) -> bool:
        cur.execute(
            "SELECT 1 FROM graph.nodes WHERE tenant_id=%s AND space_id=%s AND canonical_key=%s",
            (tenant_id, space_id, canonical_key),
        )
        return cur.fetchone() is not None

    def _repoint_edges(self, cur: Any, tenant_id: UUID, space_id: UUID, merge_id: UUID, source_ids: list[UUID], target_id: UUID) -> None:
        cur.execute(
            """SELECT id,source_node_id,target_node_id,relation_type FROM graph.edges
            WHERE tenant_id=%s AND space_id=%s AND (source_node_id = ANY(%s) OR target_node_id = ANY(%s)) FOR UPDATE""",
            (tenant_id, space_id, source_ids, source_ids),
        )
        for edge_id, s, t, relation in cur.fetchall():
            new_s = target_id if s in source_ids else s
            new_t = target_id if t in source_ids else t
            origin = s if s in source_ids else t
            if new_s == new_t:
                cur.execute("DELETE FROM graph.edges WHERE tenant_id=%s AND id=%s", (tenant_id, edge_id))
            else:
                cur.execute(
                    """SELECT 1 FROM graph.edges WHERE tenant_id=%s AND space_id=%s AND source_node_id=%s
                    AND target_node_id=%s AND relation_type=%s AND id<>%s""",
                    (tenant_id, space_id, new_s, new_t, relation, edge_id),
                )
                if cur.fetchone():
                    cur.execute("DELETE FROM graph.edges WHERE tenant_id=%s AND id=%s", (tenant_id, edge_id))
                else:
                    cur.execute(
                        "UPDATE graph.edges SET source_node_id=%s, target_node_id=%s WHERE tenant_id=%s AND id=%s",
                        (new_s, new_t, tenant_id, edge_id),
                    )
            cur.execute(
                """INSERT INTO graph.merge_edge_redirects(tenant_id,merge_id,edge_id,from_node_id,to_node_id,relation_type)
                VALUES(%s,%s,%s,%s,%s,%s)""",
                (tenant_id, merge_id, edge_id, origin, target_id, relation),
            )

    def _fold_source(self, cur: Any, tenant_id: UUID, space_id: UUID, src: dict[str, Any], target: dict[str, Any], merge_id: UUID) -> None:
        cur.execute(
            """INSERT INTO graph.node_aliases(tenant_id,node_id,alias,alias_type,confidence)
            VALUES(%s,%s,%s,'merge_source',1.0) ON CONFLICT(node_id,alias) DO NOTHING""",
            (tenant_id, target["id"], src["canonical_key"]),
        )
        cur.execute(
            """INSERT INTO knowledge.recycle_bin(tenant_id,entity_kind,entity_id,snapshot,deleted_by)
            VALUES(%s,'graph.node',%s,%s,NULL)""",
            (
                tenant_id, src["id"],
                Json({
                    "canonical_key": src["canonical_key"], "node_type": src["node_type"], "label": src["label"],
                    "properties": src["properties"], "space_id": str(space_id), "merge_into": target["canonical_key"], "merge_id": str(merge_id),
                }),
            ),
        )
        cur.execute("SELECT set_config('app.graph_revision_event', 'merged', true)")
        cur.execute(
            "UPDATE graph.nodes SET deleted_at=now(), row_version=row_version+1 WHERE tenant_id=%s AND id=%s",
            (tenant_id, src["id"]),
        )

    def _count_redirects(self, cur: Any, tenant_id: UUID, merge_id: UUID) -> int:
        cur.execute(
            "SELECT count(*) FROM graph.merge_edge_redirects WHERE tenant_id=%s AND merge_id=%s",
            (tenant_id, merge_id),
        )
        return int(cur.fetchone()[0])

    def _concurrency_conflict(self, cur: Any, tenant_id: UUID, space_key: str, node_key: str, expected: int, actual: int, op: str) -> None:
        cur.execute(
            "INSERT INTO knowledge.conflict_cases(tenant_id,entity_key,severity,summary) VALUES(%s,%s,'high',%s) RETURNING id",
            (tenant_id, node_key, f"optimistic-lock {op}: expected row_version {expected} but node is {actual} in {space_key}"),
        )
        case_row = cur.fetchone()
        cur.execute(
            "INSERT INTO knowledge.conflict_items(tenant_id,case_id,source_uri,claim) VALUES(%s,%s,%s,%s)",
            (tenant_id, case_row[0], f"graph://{space_key}/{node_key}", Json({"op": op, "expected_revision": expected, "actual_revision": actual, "space_key": space_key, "node_key": node_key})),
        )
