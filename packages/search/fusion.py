"""Cross-kind read-only search fused with reciprocal rank fusion.

One query reaches four independent sources that do not share a score space:

* ``document``  -- the knowledge corpus, via :class:`KnowledgeRetrievalService`;
* ``run``       -- ``topology.execution_runs`` matched on ``chain_key`` / id;
* ``artifact``  -- ``.data/evidence/*.zip`` bundles matched on ``run_id``;
* ``suggestion``-- ``experience.relation_suggestions`` matched on plugin ids.

Raw scores from these sources are not comparable, so each source is ranked
*independently* and the rankings are fused with the exact reciprocal rank
formula already used inside the knowledge layer (``1/(60+rank)``).  No new
weights are invented.  When two fused scores tie the more certain kind wins:
an artifact or a run is a recorded fact, a suggestion is a hypothesis, and a
document is prose -- so ``artifact/run > suggestion > document``.

The vector path is never faked.  If the embedder cannot be reached (or returns
nothing), the document group falls back to keyword ranking and ``degraded.
vector`` is set to ``True``.  Silently serving keyword results as if they were
hybrid is the exact mis-reporting this view exists to avoid.  The document
group's ``matched_by`` list (``["keyword"]`` / ``["vector"]`` /
``["keyword","vector"]``) is what tells the interface which mode actually ran.

Nothing here writes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import register_uuid

from packages.knowledge.retrieval import KnowledgeRetrievalService, reciprocal_rank_fusion
from packages.observability.run_index import list_bundles

register_uuid()  # type: ignore[no-untyped-call]

KINDS: tuple[str, ...] = ("document", "run", "artifact", "suggestion")

#: Deterministic tie-break when fused RRF scores are equal: recorded facts
#: first, then hypotheses, then prose.
_PRIORITY: dict[str, int] = {"artifact": 0, "run": 0, "suggestion": 1, "document": 2}

_RRF_CONSTANT = 60


def _tenant_id(cur: Any, tenant_slug: str) -> Any:
    cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"tenant not found: {tenant_slug}")
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(row[0]),))
    return row[0]


def _document_group(svc: KnowledgeRetrievalService, query: str, cap: int) -> tuple[list[dict[str, Any]], bool]:
    """Knowledge corpus hits plus an honest ``degraded.vector`` flag.

    Keyword ranking always runs.  Vector ranking runs next; *any* failure to
    produce vectors (embedder down, non-finite output, no rows for the current
    model) means the document group is keyword-only and the caller must surface
    ``degraded.vector=True``.  We never let a vector failure look like an empty
    vector result.
    """

    keyword = svc.search(query, mode="keyword", limit=cap)
    try:
        vector = svc._vector(query, cap)  # noqa: SLF001 - intentional reuse of the model-scoped query
    except RuntimeError:
        return keyword, True
    if not vector:
        return keyword, False
    fused = reciprocal_rank_fusion(keyword, vector, limit=cap)
    return fused, False


def _run_group(database_url: str, tenant_slug: str, query: str, cap: int) -> list[dict[str, Any]]:
    like = f"%{query}%"
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        tenant_id = _tenant_id(cur, tenant_slug)
        cur.execute(
            """SELECT id,chain_key,status,node_total,node_succeeded,node_failed,started_at
            FROM topology.execution_runs
            WHERE tenant_id=%s AND (chain_key ILIKE %s OR id::text ILIKE %s)
            ORDER BY started_at DESC NULLS LAST LIMIT %s""",
            (tenant_id, like, like, cap),
        )
        rows = cur.fetchall()
    items: list[dict[str, Any]] = []
    needle = query.lower()
    for run_id, chain_key, status, total, succeeded, failed, started in rows:
        run_id_text = str(run_id)
        matched_by: list[str] = []
        if chain_key and needle in chain_key.lower():
            matched_by.append("chain_key")
        if needle in run_id_text.lower():
            matched_by.append("id")
        items.append(
            {
                "kind": "run",
                "item_id": run_id_text,
                "title": chain_key or run_id_text[:8],
                "subtitle": f"{status} · {succeeded}/{total} ok" if total is not None else str(status),
                "anchor": {"schema": "topology", "table": "execution_runs", "pk": run_id_text},
                "matched_by": matched_by or ["id"],
            }
        )
    return items


def _sha12(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def _artifact_group(project_root: Path, query: str, cap: int) -> list[dict[str, Any]]:
    bundles, _missing = list_bundles(project_root)
    needle = query.lower()
    items: list[dict[str, Any]] = []
    for bundle in bundles:
        if needle not in bundle.run_id.lower():
            continue
        item = {
            "kind": "artifact",
            "item_id": bundle.run_id,
            "title": bundle.plan_key or bundle.run_id[:8],
            "subtitle": f"{bundle.size_bytes} bytes · {bundle.members} members",
            "anchor": {
                "path": bundle.path,
                "sha256": _sha12(project_root / bundle.path) if bundle.readable else None,
            },
            "matched_by": ["run_id"],
        }
        items.append(item)
        if len(items) >= cap:
            break
    return items


def _suggestion_group(database_url: str, tenant_slug: str, query: str, cap: int) -> list[dict[str, Any]]:
    like = f"%{query}%"
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        tenant_id = _tenant_id(cur, tenant_slug)
        cur.execute(
            """SELECT suggestion_id,source_plugin_id,target_plugin_id,contract_id,status,evidence_count
            FROM experience.relation_suggestions
            WHERE tenant_id=%s AND (source_plugin_id ILIKE %s OR target_plugin_id ILIKE %s)
            ORDER BY last_evidence_at DESC NULLS LAST LIMIT %s""",
            (tenant_id, like, like, cap),
        )
        rows = cur.fetchall()
    items: list[dict[str, Any]] = []
    needle = query.lower()
    for suggestion_id, source, target, contract, status, evidence_count in rows:
        matched_by: list[str] = []
        if source and needle in source.lower():
            matched_by.append("source_plugin")
        if target and needle in target.lower():
            matched_by.append("target_plugin")
        items.append(
            {
                "kind": "suggestion",
                "item_id": str(suggestion_id),
                "title": f"{source} → {target}",
                "subtitle": f"{status} · {evidence_count} evidence" + (f" · {contract}" if contract else ""),
                "anchor": {"schema": "experience", "table": "relation_suggestions", "pk": str(suggestion_id)},
                "matched_by": matched_by or ["source_plugin"],
            }
        )
    return items


def _fuse(groups: dict[str, list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    scored: list[tuple[float, int, str, dict[str, Any]]] = []
    for kind, rows in groups.items():
        for rank, row in enumerate(rows, start=1):
            score = 1.0 / (_RRF_CONSTANT + rank)
            scored.append((score, _PRIORITY.get(kind, 99), str(row["item_id"]), row))
    scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
    fused: list[dict[str, Any]] = []
    for score, _priority, _item_id, row in scored[:limit]:
        fused.append({**row, "score": round(score, 6)})
    return fused


def search_all(
    database_url: str,
    tenant_slug: str,
    project_root: Path,
    query: str,
    *,
    kinds: set[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Fused cross-kind read-only search.

    ``items`` carries the globally fused ranking; ``degraded.vector`` says out
    loud whether the vector path actually contributed.  An empty hit list is a
    normal answer, never an error.
    """

    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    wanted = set(kinds) if kinds else set(KINDS)
    unknown = wanted - set(KINDS)
    if unknown:
        raise ValueError(f"unknown search kinds: {sorted(unknown)}")

    cap = max(limit * 2, 10)
    groups: dict[str, list[dict[str, Any]]] = {}
    degraded_vector = False

    if "document" in wanted:
        svc = KnowledgeRetrievalService(database_url, tenant_slug)
        doc_rows, degraded_vector = _document_group(svc, query, cap)
        groups["document"] = [
            {
                "kind": "document",
                "item_id": str(row["document_id"]),
                "title": row["title"],
                "subtitle": row["source_uri"],
                "anchor": {"schema": "semantic", "table": "documents", "pk": str(row["document_id"])},
                "matched_by": list(row["matched_by"]),
            }
            for row in doc_rows
        ]
    if "run" in wanted:
        groups["run"] = _run_group(database_url, tenant_slug, query, cap)
    if "artifact" in wanted:
        groups["artifact"] = _artifact_group(project_root, query, cap)
    if "suggestion" in wanted:
        groups["suggestion"] = _suggestion_group(database_url, tenant_slug, query, cap)

    items = _fuse(groups, limit)
    return {"items": items, "degraded": {"vector": degraded_vector}}
