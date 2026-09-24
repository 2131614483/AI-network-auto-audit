"""Deterministic graph-side semantic index for orchestration planning (M10 + S3).

The knowledge graph becomes the network-orchestration index: a business intent
(in Chinese text) is scored against every active L2 capability / family / L3
domain node with pg_trgm (``similarity`` of label and aliases, lower bound 0.05,
no LLM, no external network), then expanded at most one hop with a hard cap and
full provenance.

Three entry granularities, each able to drill down one hop — this is what makes
the recall *multi-level* without widening the hop contract:

===============  ==================================================
entry            one hop reaches
===============  ==================================================
L3 domain        L2 capabilities via ``capability_contract`` bridges
L2 family        its capabilities via internal ``contains`` edges
L2 capability    neighbouring capabilities via ``depends_on``
===============  ==================================================

``expand_hops`` stays 0 or 1: "multi-level" means a coarse phrase can enter at
the family layer and still reach plugins, not that the walker takes N hops.  A
bounded walk over a 100 000-plugin graph needs the *entry* to be selectable, not
the traversal to be longer.

Recall itself is two-stage so that it stays affordable at that scale: the k
nearest nodes by label and by alias are pulled with a GiST KNN walk (``ORDER BY
label <-> q LIMIT k``, indexes from migration 0067) and only those candidates are
scored.  Scoring every node instead — with a per-row alias subquery — is a linear
scan costing 244–274 ms at 50 000 nodes, versus 6.0–6.6 ms for the walk, both
measured as the application role under RLS.

KNN rather than the ``%`` operator is deliberate.  ``similarity(a, b) >= x`` is
expressible as ``a % b`` (GIN, migration 0048), but the planner needs a
selectivity estimate to choose it, and as a non-superuser role on an RLS table
that estimate collapses to a 1 % guess (7 rows actual, 1–509 estimated at 50 000
nodes) — so the index is never used, which is exactly the linear cost it exists
to avoid.  A KNN walk needs no selectivity estimate at all.

Every query runs under the tenant RLS session variable and is strictly
read-only.

``CapabilityGraphAdapter`` is a replaceable implementation of the
``CapabilityGraphPort`` protocol consumed by ``GraphPlanningService``; an
embedding-backed matcher can be swapped in later without touching the planner.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg2

_MIN_SIMILARITY = 0.05
_MATCH_LEVELS = ("L2", "L3")  # capability/family semantics + business domain spaces
_MATCH_NODE_TYPES = ("capability", "domain", "capability_family")
_EXPANSION_CAP = 8  # per-call cap on one-hop expanded nodes
#: Same-space relations that reach a capability in one hop.  ``depends_on`` is
#: capability -> capability (derived from port-contract compatibility);
#: ``contains`` is family -> capability (derived from the domain pack's stages).
_EXPANSION_RELATIONS = ("depends_on", "contains")

#: How many candidates each KNN branch feeds to the re-ranking step.  Each branch
#: returns its ``candidates`` nearest nodes, and the union must contain the final
#: top-N or recall would silently lose matches.  ``k >= max_matches`` suffices: a
#: node outside the k nearest by *label* is beaten by k nodes with a higher label
#: score, and one outside the k nearest by *alias* is beaten by k nodes with a
#: higher alias score — either way its ``GREATEST`` cannot reach the top k.  The
#: 4x margin absorbs ties, whose mutual order the index does not define.
def _candidate_budget(max_matches: int) -> int:
    return max(max_matches * 4, 64)


#: Candidate generation (KNN), then scoring.  Each branch walks its own GiST
#: trigram index in distance order and stops after ``candidates`` rows, so the
#: cost is bounded by the budget instead of by the size of the graph — and no
#: selectivity estimate is involved (migration 0067 records why that matters:
#: under RLS the planner mis-estimates the GIN ``%`` path and abandons it).
#: ``GREATEST(label, alias) >= min`` is re-checked on the candidates, so the
#: answer keeps the exact semantics of the scan it replaces; that equivalence is
#: asserted in ``tests/integration/test_plugin_graph_recall.py``, which keeps the
#: pre-index implementation as an oracle.
_CANDIDATE_SQL = """
WITH label_hits AS (
    SELECT n.id AS node_id,
           similarity(n.label, %(q)s) AS label_score,
           0.0::real AS alias_score
    FROM graph.nodes n
    WHERE n.deleted_at IS NULL
      AND n.node_type = ANY(%(types)s)
    ORDER BY n.label <-> %(q)s
    LIMIT %(candidates)s
),
alias_hits AS (
    SELECT n.id AS node_id,
           0.0::real AS label_score,
           similarity(a.alias, %(q)s) AS alias_score
    FROM graph.node_aliases a
    JOIN graph.nodes n ON n.id = a.node_id
    WHERE n.deleted_at IS NULL
      AND n.node_type = ANY(%(types)s)
    ORDER BY a.alias <-> %(q)s
    LIMIT %(candidates)s
),
hits AS (
    SELECT node_id, label_score, alias_score FROM label_hits
    UNION ALL
    SELECT node_id, label_score, alias_score FROM alias_hits
),
scored AS (
    SELECT node_id,
           max(label_score) AS label_score,
           max(alias_score) AS alias_score
    FROM hits
    GROUP BY node_id
)
SELECT n.canonical_key, s.key, n.label, n.node_type,
       sc.label_score, sc.alias_score
FROM scored sc
JOIN graph.nodes n ON n.id = sc.node_id
JOIN graph.spaces s ON s.id = n.space_id
WHERE s.level = ANY(%(levels)s)
  AND s.status = 'active'
  AND GREATEST(sc.label_score, sc.alias_score) >= %(min)s
ORDER BY GREATEST(sc.label_score, sc.alias_score) DESC, s.key, n.canonical_key
LIMIT %(limit)s
"""


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

        Candidates come from a KNN walk of the GiST trigram indexes on ``label``
        and ``alias``; the result is identical to scoring every node, which
        ``tests/integration/test_plugin_graph_recall.py`` asserts against the
        pre-index implementation as an oracle.
        """
        if not 1 <= max_matches <= 16:
            raise ValueError("max_matches must be between 1 and 16")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                self._tenant(cur)
                cur.execute(
                    _CANDIDATE_SQL,
                    {
                        "q": intent_text,
                        "levels": list(_MATCH_LEVELS),
                        "types": list(_MATCH_NODE_TYPES),
                        "min": _MIN_SIMILARITY,
                        "limit": max_matches,
                        "candidates": _candidate_budget(max_matches),
                    },
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
        """One-hop bounded expansion, driven by which granularity matched.

        Domain matches expand via active ``capability_contract`` bridge edges
        into L2 capability nodes; capability matches expand via internal
        ``depends_on`` edges; family matches expand via internal ``contains``
        edges into the plugins that stage owns.  ``expand_hops`` is 0 (no walk)
        or 1 (one hop) — the entry granularity is what changes, not the hop
        count — and the walk is hard-capped at ``_EXPANSION_CAP`` nodes across
        all sources, with the relation followed and the originating node
        recorded on each expanded result for provenance.
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
                        # (b) same-space edges that reach a capability:
                        #     capability -> depends_on, family -> contains.
                        #     They differ only in meaning, so one query covers
                        #     both and reports which relation it followed —
                        #     provenance stays specific rather than "expanded".
                        remaining = _EXPANSION_CAP - len(expanded)
                        cur.execute(
                            """
                            SELECT tgt.canonical_key, ts.key, tgt.label, src.canonical_key,
                                   e.relation_type
                            FROM graph.edges e
                            JOIN graph.nodes src ON src.id = e.source_node_id
                            JOIN graph.nodes tgt ON tgt.id = e.target_node_id
                            JOIN graph.spaces ts ON ts.id = tgt.space_id
                            WHERE e.relation_type = ANY(%s)
                              AND e.valid_to IS NULL
                              AND src.canonical_key = ANY(%s)
                              AND src.space_id = tgt.space_id
                              AND tgt.deleted_at IS NULL
                              AND tgt.node_type = 'capability'
                              AND ts.level = 'L2'
                            ORDER BY tgt.canonical_key
                            LIMIT %s
                            """,
                            (list(_EXPANSION_RELATIONS), matched_keys, remaining),
                        )
                        for canonical_key, space_key, label, source_key, relation in cur.fetchall():
                            expanded.append(
                                {
                                    "node_key": str(canonical_key),
                                    "space_key": str(space_key),
                                    "label": str(label),
                                    "node_type": "capability",
                                    "match_kind": "expanded",
                                    "match_source": str(relation),
                                    "source_node_key": str(source_key),
                                }
                            )
                return {"expanded": expanded}
