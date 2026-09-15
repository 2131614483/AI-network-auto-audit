"""Deterministic graph-side semantic index for orchestration planning (M10).

The knowledge graph becomes the network-orchestration index: a business intent
(in Chinese text) is scored against every active L2 capability / L3 domain node
with pg_trgm (``similarity`` of label and aliases, lower bound 0.05, no LLM, no
external network), then expanded at most one hop over active
``capability_contract`` bridges (domain -> capability) and internal
``depends_on`` edges (capability -> capability), with a hard expansion cap and
full provenance.  Every query runs under the tenant RLS session variable and
is strictly read-only.

``CapabilityGraphAdapter`` is a replaceable implementation of the
``CapabilityGraphPort`` protocol consumed by ``GraphPlanningService``; an
embedding-backed matcher can be swapped in later without touching the planner.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg2

_MIN_SIMILARITY = 0.05
_MATCH_LEVELS = ("L2", "L3")  # capability semantics + business domain spaces
_MATCH_NODE_TYPES = ("capability", "domain")
_EXPANSION_CAP = 8  # per-call cap on one-hop expanded nodes


class CapabilityGraphAdapter:
    """pg_trgm intent matcher + bounded graph walker behind tenant RLS."""

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
        cur.fetchone()
        return tenant_id

    def match_nodes(self, intent_text: str, max_matches: int = 4) -> list[dict[str, Any]]:
        """Score active L2/L3 nodes against the intent; deterministic order.

        Score is ``GREATEST(similarity(label), max(similarity(alias)))`` with a
        ``0.05`` lower bound; ties break on ``(space_key, canonical_key)`` so
        two identical inputs always yield identical results.
        """
        if not 1 <= max_matches <= 16:
            raise ValueError("max_matches must be between 1 and 16")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                self._tenant(cur)
                cur.execute(
                    """
                    SELECT n.canonical_key, s.key, n.label, n.node_type,
                           similarity(n.label, %s) AS label_score,
                           COALESCE((
                             SELECT max(similarity(a.alias, %s))
                             FROM graph.node_aliases a WHERE a.node_id = n.id
                           ), 0) AS alias_score
                    FROM graph.nodes n
                    JOIN graph.spaces s ON s.id = n.space_id
                    WHERE s.level = ANY(%s)
                      AND s.status = 'active'
                      AND n.deleted_at IS NULL
                      AND n.node_type = ANY(%s)
                      AND GREATEST(
                            similarity(n.label, %s),
                            COALESCE((
                              SELECT max(similarity(a.alias, %s))
                              FROM graph.node_aliases a WHERE a.node_id = n.id
                            ), 0)
                          ) >= %s
                    ORDER BY GREATEST(
                        similarity(n.label, %s),
                        COALESCE((
                          SELECT max(similarity(a.alias, %s))
                          FROM graph.node_aliases a WHERE a.node_id = n.id
                        ), 0)
                      ) DESC, s.key, n.canonical_key
                    LIMIT %s
                    """,
                    (
                        intent_text, intent_text, list(_MATCH_LEVELS), list(_MATCH_NODE_TYPES),
                        intent_text, intent_text, _MIN_SIMILARITY,
                        intent_text, intent_text, max_matches,
                    ),
                )
                matches: list[dict[str, Any]] = []
                for canonical_key, space_key, label, node_type, label_score, alias_score in cur.fetchall():
                    label_score = float(label_score)
                    alias_score = float(alias_score)
                    score = max(label_score, alias_score)
                    if score < _MIN_SIMILARITY:
                        continue
                    matches.append(
                        {
                            "node_key": str(canonical_key),
                            "space_key": str(space_key),
                            "label": str(label),
                            "node_type": str(node_type),
                            "score": round(score, 6),
                            "match_kind": "direct" if label_score >= alias_score else "alias",
                            "match_source": "trigram",
                        }
                    )
                return matches

    def expand_to_capabilities(
        self, node_keys: list[str], expand_hops: int = 1
    ) -> dict[str, Any]:
        """One-hop bounded expansion over bridges and internal depends_on edges.

        Domain matches expand via active ``capability_contract`` bridge edges
        into L2 capability nodes; capability matches expand via internal
        ``depends_on`` edges.  ``expand_hops`` is 0 (no walk) or 1 (one hop),
        and the walk is hard-capped at ``_EXPANSION_CAP`` nodes with the source
        node recorded on each expanded result for provenance.
        """
        if expand_hops not in (0, 1):
            raise ValueError("expand_hops must be 0 or 1")
        if not node_keys:
            return {"expanded": []}
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                self._tenant(cur)
                matched: list[dict[str, Any]] = []
                cur.execute(
                    """
                    SELECT n.canonical_key, s.key, n.label, n.node_type
                    FROM graph.nodes n
                    JOIN graph.spaces s ON s.id = n.space_id
                    WHERE n.canonical_key = ANY(%s) AND n.deleted_at IS NULL
                    ORDER BY s.key, n.canonical_key
                    """,
                    (node_keys,),
                )
                for canonical_key, space_key, label, node_type in cur.fetchall():
                    matched.append(
                        {
                            "node_key": str(canonical_key),
                            "space_key": str(space_key),
                            "label": str(label),
                            "node_type": str(node_type),
                        }
                    )
                matched_keys = [item["node_key"] for item in matched]
                expanded: list[dict[str, Any]] = []
                if expand_hops == 1 and matched_keys:
                    # (a) domain matches -> active capability_contract bridge edges
                    cur.execute(
                        """
                        SELECT tgt.canonical_key, ts.key, tgt.label, src.canonical_key
                        FROM graph.bridge_edges be
                        JOIN graph.nodes src ON src.id = be.source_node_id
                        JOIN graph.nodes tgt ON tgt.id = be.target_node_id
                        JOIN graph.spaces ts ON ts.id = tgt.space_id
                        WHERE be.status = 'active'
                          AND be.relation_type = 'capability_contract'
                          AND src.canonical_key = ANY(%s)
                          AND tgt.deleted_at IS NULL
                          AND tgt.node_type = 'capability'
                          AND ts.level = 'L2'
                        ORDER BY tgt.canonical_key
                        LIMIT %s
                        """,
                        (matched_keys, _EXPANSION_CAP),
                    )
                    for canonical_key, space_key, label, source_key in cur.fetchall():
                        expanded.append(
                            {
                                "node_key": str(canonical_key),
                                "space_key": str(space_key),
                                "label": str(label),
                                "node_type": "capability",
                                "match_kind": "expanded",
                                "match_source": "bridge",
                                "source_node_key": str(source_key),
                            }
                        )
                    if len(expanded) < _EXPANSION_CAP:
                        # (b) capability matches -> internal depends_on edges
                        remaining = _EXPANSION_CAP - len(expanded)
                        cur.execute(
                            """
                            SELECT tgt.canonical_key, ts.key, tgt.label, src.canonical_key
                            FROM graph.edges e
                            JOIN graph.nodes src ON src.id = e.source_node_id
                            JOIN graph.nodes tgt ON tgt.id = e.target_node_id
                            JOIN graph.spaces ts ON ts.id = tgt.space_id
                            WHERE e.relation_type = 'depends_on'
                              AND e.valid_to IS NULL
                              AND src.canonical_key = ANY(%s)
                              AND src.space_id = tgt.space_id
                              AND tgt.deleted_at IS NULL
                              AND tgt.node_type = 'capability'
                              AND ts.level = 'L2'
                            ORDER BY tgt.canonical_key
                            LIMIT %s
                            """,
                            (matched_keys, remaining),
                        )
                        for canonical_key, space_key, label, source_key in cur.fetchall():
                            expanded.append(
                                {
                                    "node_key": str(canonical_key),
                                    "space_key": str(space_key),
                                    "label": str(label),
                                    "node_type": "capability",
                                    "match_kind": "expanded",
                                    "match_source": "depends_on",
                                    "source_node_key": str(source_key),
                                }
                            )
                return {"expanded": expanded}
