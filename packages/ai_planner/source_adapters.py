"""Bounded local evidence reads; the caller's broker owns Policy Gateway checks.

No adapter accepts model-supplied SQL, tenant names, filesystem paths or URLs.
Knowledge lookup is deliberately keyword-only and never creates an embedder.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Literal
from urllib.parse import unquote, urlparse
from uuid import UUID

import psycopg2

from packages.paths import data_root

from .evidence import (
    EvidenceItem,
    PlanningContext,
    PlanningInput,
    ResolvedInput,
    SourceKind,
    SourceQuery,
    SourceResult,
    fingerprint,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MAX_INPUT_BYTES = 64 * 1024 * 1024
_MAX_DETAIL_ROWS = 16
_RUN_LINEAGE_KEYS = frozenset({"run_id", "run_ids", "source_run_id", "source_run_ids", "evidence_run_ids"})
_LINEAGE_KEYS = frozenset({"derived_from", "source_refs", "source_ref", "origin_refs", "origin_ref"})


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    return value


def _canonical_run_ref(value: str, *, explicit_run: bool = False) -> str | None:
    """Normalize only explicit run identifiers or known database run references."""
    candidate: str | None = value if explicit_run else None
    if value.startswith("run:"):
        candidate = value[4:]
    else:
        parsed = urlparse(value)
        parts = parsed.path.strip("/").split("/")
        if parsed.scheme == "db" and not parsed.query and not parsed.fragment:
            if parsed.netloc in {"runs", "topology.execution_runs"} and len(parts) == 1:
                candidate = parts[0]
            elif parsed.netloc == "control.node_attempts" and len(parts) == 2 and parts[0] == "run":
                candidate = parts[1]
    if candidate is None:
        return None
    try:
        return f"db://runs/{UUID(candidate)}"
    except ValueError:
        return None


def _lineage_refs(value: Any) -> list[str]:
    """Extract bounded, explicitly labelled lineage; strings are never tool targets.

    Canonical run identities precede other strings so a large source_refs list
    cannot crowd the actual common-run identity out of the 32-reference budget.
    """
    runs: list[str] = []
    other: list[str] = []

    def visit(item: Any, *, capture: bool = False, run_hint: bool = False, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(item, dict):
            if str(item.get("kind") or item.get("type") or "").lower() in {"run", "execution_run"}:
                typed_id = item.get("id")
                if isinstance(typed_id, str):
                    visit(typed_id, capture=True, run_hint=True, depth=depth + 1)
            for key, nested in list(item.items())[:128]:
                if key in _RUN_LINEAGE_KEYS:
                    visit(nested, capture=True, run_hint=True, depth=depth + 1)
                elif key in _LINEAGE_KEYS or (capture and key in {"uri", "ref"}):
                    visit(nested, capture=True, depth=depth + 1)
                elif isinstance(nested, (dict, list, tuple)):
                    visit(nested, depth=depth + 1)
        elif isinstance(item, (list, tuple)):
            for nested in item[:128]:
                visit(nested, capture=capture, run_hint=run_hint, depth=depth + 1)
        elif capture and isinstance(item, str):
            ref = item.strip()
            if not ref or len(ref) > 1000:
                return
            canonical = _canonical_run_ref(ref, explicit_run=run_hint)
            if canonical:
                runs.append(canonical)
            # Keep original provenance strings as data, including untrusted
            # URLs; no code uses these strings for network or file access.
            if not run_hint or canonical is None:
                other.append(ref)

    visit(value)
    return list(dict.fromkeys([*runs, *other]))


class SqlEvidenceSources:
    """Read current graph facts, both historical run ledgers, and knowledge text."""

    def __init__(self, database_url: str, *, allowed_roots: tuple[Path, ...] | None = None) -> None:
        self.database_url = database_url
        if allowed_roots is None:
            configured = os.getenv("AUDIT_COLLABORATIVE_READ_ROOTS")
            if configured is not None:
                raw = json.loads(configured)
                if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
                    raise ValueError("AUDIT_COLLABORATIVE_READ_ROOTS must be a JSON string array")
                allowed_roots = tuple(Path(item) for item in raw)
            else:
                allowed_roots = (data_root(),)
        self.allowed_roots = tuple(root.resolve() for root in allowed_roots)

    @contextmanager
    def _cursor(self, context: PlanningContext) -> Iterator[Any]:
        connection = psycopg2.connect(self.database_url, connect_timeout=5)
        try:
            connection.set_session(readonly=True)
            with connection, connection.cursor() as cur:
                cur.execute("SELECT set_config('statement_timeout','5000',true)")
                cur.execute("SELECT set_config('app.tenant_id',%s,true)", (str(context.tenant_id),))
                # Identity comes from the authenticated context, never a model's slug.
                cur.execute("SELECT slug FROM iam.tenants WHERE id=%s", (str(context.tenant_id),))
                if cur.fetchone() is None:
                    raise ValueError("planning tenant does not exist")
                yield cur
        finally:
            connection.close()

    def query(self, context: PlanningContext, query: SourceQuery) -> SourceResult:
        try:
            with self._cursor(context) as cur:
                if query.source_kind == "graph":
                    items = self._graph(cur, context, query)
                elif query.source_kind == "history":
                    items = self._history(cur, context, query)
                else:
                    items = self._knowledge(cur, context, query)
            return SourceResult(
                source_kind=query.source_kind,
                status="available" if items else "empty",
                items=items,
                detail="keyword-only local retrieval" if query.source_kind == "knowledge" else "local SQL evidence",
            )
        except (psycopg2.Error, ValueError) as exc:
            # Never include driver messages: they can contain connection details.
            return SourceResult(
                source_kind=query.source_kind, status="error",
                detail=f"local {query.source_kind} evidence unavailable ({type(exc).__name__})",
            )

    @staticmethod
    def _item(
        context: PlanningContext, kind: SourceKind, source_ref: str, version: str,
        facts: dict[str, Any], origin_refs: list[str], *,
        authority: Literal["declared", "observed", "published", "reference", "suggested"],
        content: str | None = None,
    ) -> EvidenceItem:
        clean = _json_value(facts)
        digest = fingerprint(clean)
        text = content if content is not None else json.dumps(clean, ensure_ascii=False, sort_keys=True)
        return EvidenceItem(
            evidence_id=f"{kind}-{digest[:32]}", tenant_id=context.tenant_id,
            source_kind=kind, source_ref=source_ref, version=version, sha256=digest,
            content=text[:8000], facts=clean,
            origin_refs=list(dict.fromkeys([*origin_refs, *_lineage_refs(clean)]))[:32], authority=authority,
        )

    def _graph(self, cur: Any, context: PlanningContext, query: SourceQuery) -> list[EvidenceItem]:
        params = {
            "tenant": str(context.tenant_id), "q": query.query,
            "caps": query.capabilities, "cap_keys": [f"capability:{cap}" for cap in query.capabilities],
            "limit": query.limit,
        }
        cur.execute(
            """SELECT n.id,s.key,n.canonical_key,n.node_type,n.label,n.row_version,n.properties,
                      n.valid_from,n.valid_to
               FROM graph.nodes n JOIN graph.spaces s ON s.id=n.space_id AND s.tenant_id=n.tenant_id
               WHERE n.tenant_id=%(tenant)s AND s.status='active' AND s.level IN ('L2','L3')
                 AND n.node_type IN ('capability','capability_family','domain')
                 AND n.deleted_at IS NULL AND n.valid_from <= now()
                 AND (n.valid_to IS NULL OR n.valid_to > now())
                 AND (cardinality(%(caps)s::text[])=0 OR n.properties->>'capability'=ANY(%(caps)s)
                      OR n.canonical_key=ANY(%(cap_keys)s))
                 AND (cardinality(%(caps)s::text[])>0 OR n.label ILIKE '%%'||%(q)s||'%%'
                      OR n.canonical_key ILIKE '%%'||%(q)s||'%%'
                      OR similarity(n.label,%(q)s)>0.05
                      OR EXISTS(SELECT 1 FROM graph.node_aliases a WHERE a.node_id=n.id
                                AND a.tenant_id=n.tenant_id AND similarity(a.alias,%(q)s)>0.05))
               ORDER BY similarity(n.label,%(q)s) DESC,s.key,n.canonical_key LIMIT %(limit)s""",
            params,
        )
        return self._graph_items(cur, context, cur.fetchall())

    def _graph_items(self, cur: Any, context: PlanningContext, rows: list[Any]) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        for row in rows:
            node_id = str(row[0])
            cur.execute(
                """SELECT e.id,e.relation_type,e.row_version,e.properties,e.valid_from,e.valid_to,
                          src.id,src.canonical_key,src.row_version,tgt.id,tgt.canonical_key,tgt.row_version
                   FROM graph.edges e
                   JOIN graph.nodes src ON src.id=e.source_node_id AND src.tenant_id=e.tenant_id
                   JOIN graph.nodes tgt ON tgt.id=e.target_node_id AND tgt.tenant_id=e.tenant_id
                   JOIN graph.spaces s ON s.id=e.space_id AND s.tenant_id=e.tenant_id
                   WHERE e.tenant_id=%s AND (e.source_node_id=%s OR e.target_node_id=%s)
                     AND s.status='active' AND e.deleted_at IS NULL AND e.valid_from<=now()
                     AND (e.valid_to IS NULL OR e.valid_to>now())
                     AND src.deleted_at IS NULL AND tgt.deleted_at IS NULL
                     AND src.valid_from<=now() AND tgt.valid_from<=now()
                     AND (src.valid_to IS NULL OR src.valid_to>now())
                     AND (tgt.valid_to IS NULL OR tgt.valid_to>now())
                   ORDER BY e.relation_type,src.canonical_key,tgt.canonical_key LIMIT 8""",
                (str(context.tenant_id), node_id, node_id),
            )
            relations = [
                {"edge_id": str(r[0]), "relation_type": r[1], "row_version": r[2],
                 "properties": r[3], "valid_from": r[4], "valid_to": r[5],
                 "source_node_id": str(r[6]), "source_key": r[7], "source_version": r[8],
                 "target_node_id": str(r[9]), "target_key": r[10], "target_version": r[11]}
                for r in cur.fetchall()
            ]
            facts = {
                "node_id": node_id, "space_key": row[1], "node_key": row[2], "node_type": row[3],
                "label": row[4], "row_version": row[5], "properties": row[6],
                "valid_from": row[7], "valid_to": row[8], "relations": relations,
                "relation_scope": "current internal relations, at most 8 per node",
            }
            source_ref = f"db://graph.nodes/{node_id}"
            origins = [source_ref, *(f"db://graph.edges/{r['edge_id']}" for r in relations)]
            items.append(self._item(context, "graph", source_ref, str(row[5]), facts, origins, authority="declared"))
        return items

    def _history(self, cur: Any, context: PlanningContext, query: SourceQuery) -> list[EvidenceItem]:
        params = {
            "tenant": str(context.tenant_id), "q": query.query,
            "caps": query.capabilities, "limit": query.limit,
        }
        cur.execute(
            """WITH latest AS (
                   SELECT DISTINCT ON (run_id,node_instance_id) * FROM control.node_attempts
                   WHERE tenant_id=%(tenant)s ORDER BY run_id,node_instance_id,attempt_seq DESC
               )
               SELECT run_id,MAX(plan_key),MAX(execution_hash),MIN(created_at),MAX(finished_at),
                      MAX(trace_id),COUNT(*),COUNT(*) FILTER(WHERE status='succeeded'),
                      COUNT(*) FILTER(WHERE status='failed'),array_agg(DISTINCT capability)
               FROM latest GROUP BY run_id
               HAVING bool_and(status IN ('succeeded','failed','cancelled'))
                 AND (cardinality(%(caps)s::text[])=0 OR bool_or(capability=ANY(%(caps)s)))
                 AND (cardinality(%(caps)s::text[])>0 OR run_id::text ILIKE '%%'||%(q)s||'%%'
                      OR bool_or(plan_key ILIKE '%%'||%(q)s||'%%'
                                 OR capability ILIKE '%%'||%(q)s||'%%'
                                 OR plugin_id ILIKE '%%'||%(q)s||'%%'))
               ORDER BY MIN(created_at) DESC,run_id LIMIT %(limit)s""", params,
        )
        selected = [
            {"surface": "cw3", "run_id": str(r[0]), "plan_key": r[1], "execution_hash": r[2],
             "created_at": r[3], "finished_at": r[4], "source_trace_id": r[5],
             "node_total": r[6], "node_succeeded": r[7], "node_failed": r[8],
             "status": "failed" if r[8] else ("succeeded" if r[7] == r[6] else "cancelled"),
             "capabilities": list(r[9] or [])}
            for r in cur.fetchall()
        ]
        cur.execute(
            """SELECT r.id,r.chain_id,r.chain_key,r.chain_checksum,r.planner_version,r.status,
                      r.created_at,r.finished_at,r.trace_id,r.node_total,r.node_succeeded,r.node_failed,
                      r.reason,COALESCE((SELECT array_agg(DISTINCT i.capability)
                          FROM topology.invocation_intents i
                          WHERE i.tenant_id=r.tenant_id AND i.chain_id=r.chain_id),ARRAY[]::text[])
               FROM topology.execution_runs r WHERE r.tenant_id=%(tenant)s AND r.status IN ('success','failed')
                 AND (cardinality(%(caps)s::text[])=0 OR EXISTS(
                     SELECT 1 FROM topology.invocation_intents i WHERE i.tenant_id=r.tenant_id
                     AND i.chain_id=r.chain_id AND i.capability=ANY(%(caps)s)))
                 AND (cardinality(%(caps)s::text[])>0 OR r.chain_key ILIKE '%%'||%(q)s||'%%'
                      OR r.id::text ILIKE '%%'||%(q)s||'%%' OR r.reason ILIKE '%%'||%(q)s||'%%'
                      OR EXISTS(SELECT 1 FROM topology.invocation_intents i
                          WHERE i.tenant_id=r.tenant_id AND i.chain_id=r.chain_id
                            AND i.capability ILIKE '%%'||%(q)s||'%%'))
               ORDER BY r.created_at DESC,r.id LIMIT %(limit)s""", params,
        )
        selected.extend(
            {"surface": "m6", "run_id": str(r[0]), "chain_id": str(r[1]), "chain_key": r[2],
             "chain_checksum": r[3], "planner_version": r[4],
             "status": "succeeded" if r[5] == "success" else "failed", "created_at": r[6],
             "finished_at": r[7], "source_trace_id": r[8], "node_total": r[9],
             "node_succeeded": r[10], "node_failed": r[11], "reason": r[12],
             "capabilities": list(r[13] or [])}
            for r in cur.fetchall()
        )
        selected.sort(key=lambda r: (r["created_at"].isoformat(), r["surface"], r["run_id"]), reverse=True)
        items: list[EvidenceItem] = []
        for facts in selected[:query.limit]:
            if facts["surface"] == "cw3":
                cur.execute(
                    """SELECT DISTINCT ON (node_instance_id) attempt_id,node_instance_id,capability,
                              plugin_id,plugin_version,runtime_code_sha256,status,error_kind,error_message,
                              input_bindings,output_refs,attempt_seq
                       FROM control.node_attempts WHERE tenant_id=%s AND run_id=%s
                       ORDER BY node_instance_id,attempt_seq DESC LIMIT %s""",
                    (str(context.tenant_id), facts["run_id"], _MAX_DETAIL_ROWS),
                )
                nodes = [
                    {"attempt_id": str(r[0]), "node_instance_id": r[1], "capability": r[2],
                     "plugin_id": r[3], "plugin_version": r[4], "runtime_code_sha256": r[5],
                     "status": r[6], "error_kind": r[7], "error_message": r[8],
                     "input_bindings": r[9], "output_refs": r[10], "attempt_seq": r[11]}
                    for r in cur.fetchall()
                ]
                source_ref = f"db://control.node_attempts/run/{facts['run_id']}"
                origins = [source_ref, *(f"db://control.node_attempts/{n['attempt_id']}" for n in nodes)]
                version = str(facts["execution_hash"])
            else:
                cur.execute(
                    """SELECT e.id,e.plan_node_slot_key,e.plugin_id,e.plugin_version,
                              e.runtime_code_sha256,e.status,e.input_refs,e.output_refs,
                              e.input_sha256,e.output_checksum,e.output_artifact_refs
                       FROM topology.execution_ledger e WHERE e.tenant_id=%s AND e.run_id=%s
                       ORDER BY e.ordinal,e.id LIMIT %s""",
                    (str(context.tenant_id), facts["run_id"], _MAX_DETAIL_ROWS),
                )
                nodes = [
                    {"ledger_id": str(r[0]), "slot_key": r[1], "plugin_id": r[2], "plugin_version": r[3],
                     "runtime_code_sha256": r[4], "status": r[5], "input_refs": r[6], "output_refs": r[7],
                     "input_sha256": r[8], "output_checksum": r[9], "output_artifact_refs": r[10]}
                    for r in cur.fetchall()
                ]
                source_ref = f"db://topology.execution_runs/{facts['run_id']}"
                origins = [source_ref, *(f"db://topology.execution_ledger/{n['ledger_id']}" for n in nodes)]
                version = str(facts["chain_checksum"])
            facts["nodes"] = nodes
            facts["node_details_partial"] = facts["node_total"] > len(nodes)
            items.append(self._item(context, "history", source_ref, version, facts, origins, authority="observed"))
        return items

    def _knowledge(self, cur: Any, context: PlanningContext, query: SourceQuery) -> list[EvidenceItem]:
        cur.execute(
            """SELECT c.id,d.id,d.title,d.source_uri,c.ordinal,c.content,c.document_version,
                      c.metadata,d.content_sha256,d.source_artifact_id,d.status,
                      GREATEST(ts_rank_cd(c.search_vector,websearch_to_tsquery('simple',%s)),
                               similarity(c.content,%s)) AS score
               FROM semantic.chunks c JOIN semantic.documents d
                 ON d.id=c.document_id AND d.tenant_id=c.tenant_id
               WHERE c.tenant_id=%s AND c.document_version=d.current_version AND d.status<>'retired'
                 AND (c.search_vector @@ websearch_to_tsquery('simple',%s)
                      OR c.content ILIKE '%%'||%s||'%%' OR similarity(c.content,%s)>0.05)
               ORDER BY score DESC,c.id LIMIT %s""",
            (query.query, query.query, str(context.tenant_id), query.query, query.query, query.query, query.limit),
        )
        return self._knowledge_items(context, cur.fetchall())

    def _knowledge_items(self, context: PlanningContext, rows: list[Any]) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        for row in rows:
            source_ref = f"db://semantic.documents/{row[1]}/version/{row[6]}"
            origins = [f"db://semantic.chunks/{row[0]}", source_ref, str(row[3])]
            if row[9]:
                origins.append(f"db://artifact.artifacts/{row[9]}")
            facts = {
                "chunk_id": str(row[0]), "document_id": str(row[1]), "title": row[2],
                "source_uri": row[3], "ordinal": row[4], "document_version": row[6],
                "chunk_sha256": hashlib.sha256(row[5].encode("utf-8")).hexdigest(),
                "metadata": row[7], "source_sha256": row[8], "source_artifact_id": str(row[9]) if row[9] else None,
                "document_status": row[10], "score": float(row[11]), "retrieval_mode": "keyword",
            }
            items.append(self._item(
                context, "knowledge", source_ref, str(row[6]), facts, origins,
                authority="reference", content=row[5],
            ))
        return items

    def validate_evidence(self, context: PlanningContext, items: list[EvidenceItem]) -> list[str]:
        """Re-read exact source references before an assessment can be accepted.

        Scores are retrieval metadata, so the knowledge query reuses the recorded
        score while re-hashing the actual current source fields and full text.
        An unavailable source never counts as a successful freshness check.
        """
        issues: list[str] = []
        try:
            with self._cursor(context) as cur:
                for item in items:
                    if item.tenant_id != context.tenant_id:
                        issues.append(f"cross_tenant_evidence:{item.evidence_id}")
                        continue
                    if fingerprint(item.facts) != item.sha256:
                        issues.append(f"evidence_fingerprint_mismatch:{item.evidence_id}")
                        continue
                    refreshed = self._refresh_item(cur, context, item)
                    if refreshed is None:
                        issues.append(f"evidence_missing_or_invalid:{item.evidence_id}")
                    elif (refreshed.sha256 != item.sha256 or refreshed.version != item.version
                          or refreshed.source_ref != item.source_ref or refreshed.content != item.content
                          or refreshed.origin_refs != item.origin_refs):
                        issues.append(f"evidence_stale_or_modified:{item.evidence_id}")
        except (psycopg2.Error, ValueError, TypeError, KeyError):
            issues.extend(f"evidence_freshness_unavailable:{item.evidence_id}" for item in items)
        return list(dict.fromkeys(issues))

    def _refresh_item(
        self, cur: Any, context: PlanningContext, item: EvidenceItem,
    ) -> EvidenceItem | None:
        parsed = urlparse(item.source_ref)
        parts = parsed.path.strip("/").split("/")
        if parsed.scheme != "db" or parsed.query or parsed.fragment:
            return None
        if item.source_kind == "graph" and parsed.netloc == "graph.nodes" and len(parts) == 1:
            node_id = str(UUID(parts[0]))
            cur.execute(
                """SELECT n.id,s.key,n.canonical_key,n.node_type,n.label,n.row_version,n.properties,
                          n.valid_from,n.valid_to FROM graph.nodes n
                   JOIN graph.spaces s ON s.id=n.space_id AND s.tenant_id=n.tenant_id
                   WHERE n.id=%s AND n.tenant_id=%s AND s.status='active'
                     AND s.level IN ('L2','L3') AND n.deleted_at IS NULL AND n.valid_from<=now()
                     AND (n.valid_to IS NULL OR n.valid_to>now())""",
                (node_id, str(context.tenant_id)),
            )
            refreshed = self._graph_items(cur, context, cur.fetchall())
            return refreshed[0] if refreshed else None
        if item.source_kind == "history":
            run_id: str | None = None
            if parsed.netloc == "control.node_attempts" and len(parts) == 2 and parts[0] == "run":
                run_id = str(UUID(parts[1]))
            elif parsed.netloc == "topology.execution_runs" and len(parts) == 1:
                run_id = str(UUID(parts[0]))
            if run_id is None:
                return None
            refreshed = self._history(cur, context, SourceQuery(
                source_kind="history", query=run_id, purpose="validate exact historical run snapshot", limit=8,
            ))
            return next((row for row in refreshed if row.source_ref == item.source_ref), None)
        if (item.source_kind == "knowledge" and parsed.netloc == "semantic.documents"
                and len(parts) == 3 and parts[1] == "version"):
            document_id, version = str(UUID(parts[0])), int(parts[2])
            chunk_id = str(UUID(str(item.facts["chunk_id"])))
            cur.execute(
                """SELECT c.id,d.id,d.title,d.source_uri,c.ordinal,c.content,c.document_version,
                          c.metadata,d.content_sha256,d.source_artifact_id,d.status,%s::float
                   FROM semantic.chunks c JOIN semantic.documents d
                     ON d.id=c.document_id AND d.tenant_id=c.tenant_id
                   WHERE c.tenant_id=%s AND d.id=%s AND c.id=%s
                     AND d.current_version=%s AND c.document_version=d.current_version
                     AND d.status<>'retired'""",
                (float(item.facts["score"]), str(context.tenant_id), document_id, chunk_id, version),
            )
            refreshed = self._knowledge_items(context, cur.fetchall())
            return refreshed[0] if refreshed else None
        return None

    def resolve_inputs(
        self, context: PlanningContext, inputs: list[PlanningInput],
    ) -> list[ResolvedInput]:
        results: list[ResolvedInput] = []
        try:
            with self._cursor(context) as cur:
                for item in inputs:
                    cur.execute(
                        """SELECT a.blob_sha256,b.byte_size,b.storage_uri FROM artifact.artifacts a
                           JOIN artifact.blobs b ON b.sha256=a.blob_sha256
                           WHERE a.tenant_id=%s AND a.id=%s""",
                        (str(context.tenant_id), str(item.artifact_id)),
                    )
                    row = cur.fetchone()
                    sha = str(row[0]) if row else "0" * 64
                    available, detail = self._verify_local_blob(row)
                    results.append(ResolvedInput(
                        **item.model_dump(), sha256=sha, available=available, detail=detail,
                    ))
        except (psycopg2.Error, ValueError):
            return [ResolvedInput(**item.model_dump(), sha256="0" * 64, available=False,
                                  detail="registered input metadata is unavailable") for item in inputs]
        return results

    def _verify_local_blob(self, row: Any) -> tuple[bool, str]:
        if row is None:
            return False, "artifact is missing from the current tenant"
        expected_sha, expected_size, uri = row
        parsed = urlparse(str(uri))
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            return False, "registered artifact must use a local file URI"
        if not 0 <= expected_size <= _MAX_INPUT_BYTES:
            return False, "registered artifact exceeds the 64 MiB verification limit"
        raw_path = unquote(parsed.path)
        if raw_path.startswith(("//", "\\\\")):
            return False, "network file paths are not allowed"
        if os.name == "nt" and len(raw_path) > 2 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        try:
            candidate = Path(os.path.abspath(raw_path))
            root = next((root for root in self.allowed_roots if candidate.is_relative_to(root)), None)
            if root is None:
                return False, "registered artifact is outside configured read roots"
            # Reject links/junctions before following them: a registered file
            # must not turn a local read into a UNC/network traversal.
            current = root
            for component in candidate.relative_to(root).parts:
                current /= component
                info = current.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    return False, "linked artifact paths are not allowed"
            path = candidate.resolve(strict=True)
            if not path.is_relative_to(root):
                return False, "registered artifact escaped configured read roots"
            if not path.is_file() or path.stat().st_size != expected_size:
                return False, "registered artifact is unavailable or its size changed"
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as source:
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > _MAX_INPUT_BYTES:
                        return False, "artifact grew beyond the verification limit"
                    digest.update(block)
            if size != expected_size or digest.hexdigest() != expected_sha:
                return False, "registered artifact failed full SHA256 verification"
        except (OSError, ValueError):
            return False, "registered artifact is not accessible"
        return True, "registered local artifact verified by full SHA256"
