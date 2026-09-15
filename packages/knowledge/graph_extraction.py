"""Phase 5: change-set governed automatic graph extraction.

The extractor is deliberately deterministic and rule-based.  It takes ingested
text documents (``semantic.documents`` / ``semantic.chunks``, optionally with
Phase 3 page back-links) and produces *candidate* graph nodes/edges.  No
candidate is ever written directly to the active graph: proposals land in a
shadow ``ChangeSet`` that must pass validate → approve → release → activate.
Conflicts / duplicate / out-of-scope claims are routed to the conflict inbox,
never auto-resolved.

Boundary: no LLM, no new MinerU/OCR scope, no audio/video, no network, no
fused live-graph writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from uuid import UUID

from jsonschema import Draft202012Validator

from packages.knowledge.lifecycle import ChangeOperation, KnowledgeLifecycleService

_SCHEMA_PATH = "contracts/jsonschema/graph-extraction.schema.json"

# Explicitly registered relation vocabulary.  Anything else is a conflict.
REGISTERED_RELATIONS = frozenset({
    "regulated_by", "owns", "part_of", "depends_on", "references", "evidence_for",
})

# A node key must be namespaced ``domain:key`` to avoid cross-space collisions.
_NODE_KEY_RE = re.compile(r"^[a-z][a-z0-9._-]*:[^\s:]+$")

# Simple entity matchers used by the deterministic extractor.  These are
# deliberately small and auditable — not a learned model.
_PATTERNS = {
    "company": re.compile(r"\b(ACME|Globex|Initech|Umbrella|Wonka)\b", re.IGNORECASE),
    "regulator": re.compile(r"\b(SEC|FINRA|FCA|MAS|CSRC)\b", re.IGNORECASE),
}
_RELATION_PATTERNS = {
    "regulated_by": re.compile(r"\b(regulated by|subject to (the )?\w+ of)\b", re.IGNORECASE),
    "part_of": re.compile(r"\b(part of|subsidiary of|unit of)\b", re.IGNORECASE),
    "depends_on": re.compile(r"\b(depends on|relies on|requires)\b", re.IGNORECASE),
}


@dataclass(frozen=True, slots=True)
class GraphExtractionCandidate:
    document_id: UUID
    source_uri: str
    space_key: str
    node_key: str
    node_type: str
    label: str
    property_source: dict[str, Any]
    relation_type: str | None = None
    target_node_key: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "document_id": str(self.document_id),
            "source_uri": self.source_uri,
            "space_key": self.space_key,
            "node_key": self.node_key,
            "node_type": self.node_type,
            "label": self.label,
            "property_source": self.property_source,
            "confidence": self.confidence,
        }
        if self.relation_type is not None:
            out["relation_type"] = self.relation_type
            if self.target_node_key is not None:
                out["target_node_key"] = self.target_node_key
        if self.properties:
            out["properties"] = self.properties
        return out


def _load_schema() -> dict[str, Any]:
    import json

    from packages.plugin_runtime.runner import PROJECT_ROOT

    path = PROJECT_ROOT / _SCHEMA_PATH
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


_SCHEMA: dict[str, Any] | None = None


def _validator() -> Draft202012Validator:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = _load_schema()
    return Draft202012Validator(_SCHEMA)


def validate_candidate(candidate: GraphExtractionCandidate) -> tuple[bool, tuple[str, ...]]:
    """Contract check — rejects bad relation / bad space / bad node key."""
    violations: list[str] = []
    if candidate.relation_type is not None and candidate.relation_type not in REGISTERED_RELATIONS:
        violations.append(f"unregistered relation type: {candidate.relation_type}")
    if not _NODE_KEY_RE.match(candidate.node_key):
        violations.append(f"node_key must be namespaced 'domain:key': {candidate.node_key}")
    if candidate.relation_type is not None and not candidate.target_node_key:
        violations.append("relation requires target_node_key")
    if candidate.confidence < 0 or candidate.confidence > 1:
        violations.append("confidence must be between 0 and 1")
    if not violations:
        errors = _validator().iter_errors(candidate.to_json())
        violations.extend(f"schema: {' / '.join(e.path) or e.message}" for e in errors)
    return (not violations), tuple(violations)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    candidates: tuple[GraphExtractionCandidate, ...] = ()
    conflicts: tuple[dict[str, Any], ...] = ()
    rejected: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.candidates or self.conflicts)


def extract_text(title: str, body: str, *, document_id: UUID, source_uri: str, space_key: str) -> Iterable[GraphExtractionCandidate]:
    """Deterministic rule-based extraction of candidate nodes + edges.

    Returns candidates exactly; conflict decisions (vs the graph) are made in
    :func:`route_candidates` where the existing space state is known.
    """
    seen_nodes: dict[str, tuple[str, str, dict[str, Any]]] = {}
    if title:
        for node_type, pattern in _PATTERNS.items():
            matched = pattern.findall(f"{title} {body}")
            for raw in matched:
                key = raw.lower()
                seen_nodes.setdefault(f"{node_type}:{key}", (node_type, raw.title(), {}))
    for node_type, pattern in _PATTERNS.items():
        for raw in pattern.findall(body):
            key = raw.lower()
            seen_nodes.setdefault(f"{node_type}:{key}", (node_type, raw.title(), {}))
    for node_key, (node_type, label, props) in seen_nodes.items():
        yield GraphExtractionCandidate(
            document_id=document_id,
            source_uri=source_uri,
            space_key=space_key,
            node_key=node_key,
            node_type=node_type,
            label=label,
            property_source={"title": title, "source_uri": source_uri},
            properties=props,
            confidence=0.9,
        )
    for relation, pattern in _RELATION_PATTERNS.items():
        if pattern.search(body):
            sources = [f"{t}:{k}" for t in _PATTERNS for k in _PATTERNS[t].findall(f"{title} {body}")]
            for node_key in sources:
                yield GraphExtractionCandidate(
                    document_id=document_id,
                    source_uri=source_uri,
                    space_key=space_key,
                    node_key=node_key,
                    node_type=node_key.split(":", 1)[0],
                    label=node_key.split(":", 1)[1].title(),
                    property_source={"title": title, "source_uri": source_uri, "relation": relation},
                    relation_type=relation,
                    target_node_key="regulator:global",
                    confidence=0.6,
                )


class GraphExtractionService:
    """Coordinates candidate routing (spaces/conflicts) and shadow ChangeSet staging."""

    def __init__(self, database_url: str, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.tenant_slug = tenant_slug

    def route_candidates(
        self,
        candidates: Iterable[GraphExtractionCandidate],
        *,
        allow_spaces: set[str] | None = None,
    ) -> ExtractionResult:
        """Split candidates into stageable vs conflict, and validate against live graph."""
        import psycopg2

        allowed = allow_spaces or {"audit-l1", "audit-l3", "quant-l3", "aiops-l3"}
        staged: list[GraphExtractionCandidate] = []
        conflicts: list[dict[str, Any]] = []
        rejected: list[str] = []
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = _tenant_id(cur, self.tenant_slug)
            for candidate in candidates:
                ok, violations = validate_candidate(candidate)
                if not ok:
                    rejected.append(f"{candidate.node_key}: {'; '.join(violations)}")
                    continue
                if candidate.space_key not in allowed:
                    conflicts.append({
                        "entity_key": candidate.node_key,
                        "severity": "high",
                        "summary": f"nomination to unregistered space: {candidate.space_key}",
                        "source_uri": candidate.source_uri,
                        "claim": candidate.to_json(),
                    })
                    continue
                # duplicate / conflicting node check against active graph
                cur.execute(
                    """SELECT 1 FROM graph.nodes n
                    JOIN graph.spaces s ON s.id=n.space_id
                    WHERE n.tenant_id=%s AND s.tenant_id=%s AND s.key=%s AND n.canonical_key=%s
                      AND n.deleted_at IS NULL""",
                    (tenant_id, tenant_id, candidate.space_key, candidate.node_key),
                )
                if cur.fetchone() is not None:
                    conflicts.append({
                        "entity_key": candidate.node_key,
                        "severity": "medium",
                        "summary": f"active node already exists in {candidate.space_key}",
                        "source_uri": candidate.source_uri,
                        "claim": candidate.to_json(),
                    })
                    continue
                staged.append(candidate)
        return ExtractionResult(tuple(staged), tuple(conflicts), tuple(rejected))

    def stage_proposal(self, candidates: Iterable[GraphExtractionCandidate], title: str, reason: str, *, graph_space_key: str, source_batch_id: UUID | None = None) -> UUID:
        """Build a shadow ChangeSet from validated candidates (still draft, not applied).

        Candidates are de-duplicated by (space, node_key) keeping the highest
        confidence, so a node extracted directly and again via a relation does
        not produce a duplicate ``create`` operation.
        """
        deduped: dict[tuple[str, str], GraphExtractionCandidate] = {}
        for candidate in candidates:
            key = (candidate.space_key, candidate.node_key)
            if key not in deduped or candidate.confidence > deduped[key].confidence:
                deduped[key] = candidate
        lifecycle = KnowledgeLifecycleService(self.database_url, self.tenant_slug)
        ops: list[ChangeOperation] = []
        for (_space, _key), candidate in deduped.items():
            ops.append(
                ChangeOperation(
                    "graph.node",
                    "create",
                    {
                        "space_key": candidate.space_key,
                        "canonical_key": candidate.node_key,
                        "node_type": candidate.node_type,
                        "label": candidate.label,
                        "properties": {**candidate.properties, "extract_source": candidate.source_uri, "extract_target": candidate.target_node_key} if candidate.target_node_key else {**candidate.properties, "extract_source": candidate.source_uri},
                    },
                )
            )
        return lifecycle.create_changeset(title, reason, ops, graph_space_key=graph_space_key, risk_class="low", source_batch_id=source_batch_id)


def _tenant_id(cur: Any, tenant_slug: str) -> UUID:
    cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"tenant not found: {tenant_slug}")
    tenant_id = UUID(str(row[0]))
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
    return tenant_id