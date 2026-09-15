"""Generate synthetic text documents in the test database and run the Phase 5
governed graph-extraction pipeline as a Golden Query.

The pipeline is intentionally graph-write-gated: the deterministic regex
extractor emits candidates, they are routed (conflicts vs. stageable) and only
staged into a shadow ChangeSet.  Nothing is written to the active graph until a
proposal is approved (validate → approve → release → activate).

Like ``benchmark_graph_routing.py`` this is intentionally additive: every run
uses a UUID-isolated document batch and a UUID-prefixed graph space, and never
deletes rows.  It never touches the interactive ``audit_network`` database.
"""

from __future__ import annotations

import argparse
import json
import os
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import psycopg2

from packages.graph.service import GraphService
from packages.knowledge.graph_extraction import GraphExtractionService, extract_text


def percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * 0.95)))
    return ordered[index]


def require_test_database(database_url: str) -> None:
    parsed = urlparse(database_url)
    if parsed.path.rstrip("/") != "/audit_network_test":
        raise SystemExit("Golden Query only permits audit_network_test; no interactive database was touched.")


def locate_tenant(cur: Any) -> UUID:
    cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("local-dev tenant not found")
    tenant_id = UUID(str(row[0]))
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
    return tenant_id


def build_synthetic_documents(database_url: str, document_count: int, suffix: str) -> list[UUID]:
    """Insert ``document_count`` deterministic text documents as semantic.documents + chunks.

    Each document mentions a rotating mix of known entities and relation phrases so
    the deterministic extractor produces a measurable, stable candidate stream.
    """
    companies = ["ACME", "Globex", "Initech", "Umbrella", "Wonka"]
    regulators = ["SEC", "FINRA", "FCA", "MAS", "CSRC"]
    relations = [
        "is regulated by the %s",
        "depends on company %s",
        "is subject to the oversight of %s",
        "is part of the %s framework",
    ]
    document_ids: list[UUID] = []
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            tenant_id = locate_tenant(cur)
            for index in range(document_count):
                company = companies[index % len(companies)]
                regulator = regulators[(index * 3) % len(regulators)]
                second_company = companies[(index + 1) % len(companies)]
                doc_id = uuid4()
                chunk = " ".join(
                    [
                        f"{company} ({regulator}-regulated) and {second_company}.",
                        relations[(index * 2) % len(relations)]
                        % (regulator if (index * 2) % len(relations) % 2 != 0 else company),
                    ]
                )
                cur.execute(
                    """INSERT INTO semantic.documents
                    (id,tenant_id,source_uri,title,status,current_version,content_sha256,source_artifact_id)
                    VALUES(%s,%s,%s,%s,'staged',1,%s,NULL)""",
                    (
                        doc_id, tenant_id,
                        f"file://golden/{suffix}/synthetic-{index}.md",
                        f"synthetic-{index}",
                        f"golden-{suffix}-{index}",
                    ),
                )
                cur.execute(
                    """INSERT INTO semantic.chunks
                    (tenant_id,document_id,document_version,ordinal,content,token_count,metadata)
                    VALUES(%s,%s,1,0,%s,%s,%s::jsonb)""",
                    (tenant_id, doc_id, chunk, len(chunk.split()), json.dumps({"source": "golden-synthetic"})),
                )
                document_ids.append(doc_id)
    return document_ids


def count_active_nodes(cur: Any, tenant_id: UUID, space_key: str, node_keys: set[str]) -> int:
    if not node_keys:
        return 0
    cur.execute(
        """SELECT count(*) FROM graph.nodes n JOIN graph.spaces s ON s.id=n.space_id
        WHERE n.tenant_id=%s AND s.key=%s AND n.canonical_key = ANY(%s) AND n.deleted_at IS NULL""",
        (tenant_id, space_key, sorted(node_keys)),
    )
    return int(cur.fetchone()[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ChangeSet-governed extraction Golden Query in audit_network_test only.")
    parser.add_argument("--database-url", default=os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test"))
    parser.add_argument("--documents", type=int, default=500)
    parser.add_argument("--staging-budget-ms", type=float, default=2_000)
    parser.add_argument("--release-budget-ms", type=float, default=3_000)
    args = parser.parse_args()
    if not 10 <= args.documents <= 20_000:
        raise SystemExit("require 10 <= documents <= 20000")
    require_test_database(args.database_url)

    suffix = uuid4().hex
    space_key = f"golden-extract-{suffix}"

    # ------------------------------------------------------------------ fixture
    per_doc: list[float] = []
    started = perf_counter()
    document_ids = build_synthetic_documents(args.database_url, args.documents, suffix)
    per_doc.append((perf_counter() - started) * 1000)

    GraphService(args.database_url).ensure_space(space_key, "L1", "Golden extraction space")
    service = GraphExtractionService(args.database_url)

    # ------------------------------------------------------- extraction pipeline
    # 1. extract candidates (read-only)
    extract_started = perf_counter()
    with psycopg2.connect(args.database_url) as connection, connection.cursor() as cur:
        tenant_id = locate_tenant(cur)
        all_candidates: list[Any] = []
        for doc_id in document_ids:
            cur.execute(
                "SELECT title,source_uri,content FROM semantic.documents d "
                "JOIN semantic.chunks c ON c.document_id=d.id AND c.document_version=d.current_version "
                "WHERE d.tenant_id=%s AND d.id=%s AND d.status='staged'",
                (tenant_id, doc_id),
            )
            for title, source_uri, content in cur.fetchall():
                all_candidates.extend(
                    extract_text(title or "(untitled)", content or "", document_id=doc_id, source_uri=source_uri, space_key=space_key)
                )
    extraction_ms = (perf_counter() - extract_started) * 1000

    # 2. route candidates (split conflicts vs stageable)
    routed = service.route_candidates(all_candidates, allow_spaces={space_key})
    stageable = routed.candidates

    # 3. stage a single proposal ChangeSet (shadow, still draft)
    staging_ms = 0.0
    changeset_id: UUID | None = None
    if stageable:
        staging_started = perf_counter()
        changeset_id = service.stage_proposal(
            stageable,
            f"Golden extraction ({suffix})",
            "Golden Query synthetic-document extraction",
            graph_space_key=space_key,
        )
        staging_ms = (perf_counter() - staging_started) * 1000

    # ------- gate: nothing may be live before approval -----------------------------
    node_keys = {c.node_key for c in stageable}
    with psycopg2.connect(args.database_url) as connection, connection.cursor() as cur:
        tenant_id = locate_tenant(cur)
        pre_live = count_active_nodes(cur, tenant_id, space_key, node_keys)
    if changeset_id is not None and pre_live != 0:
        raise SystemExit(f"gate failed: {pre_live} staged-only nodes leaked before approval")

    # ------- approve (release) the governed proposal ----------------------------
    from packages.knowledge.lifecycle import KnowledgeLifecycleService

    lifecycle = KnowledgeLifecycleService(args.database_url)
    release_ms = 0.0
    if changeset_id is not None:
        release_started = perf_counter()
        lifecycle.apply_changeset(changeset_id)
        release_ms = (perf_counter() - release_started) * 1000

    node_keys = {c.node_key for c in stageable}
    with psycopg2.connect(args.database_url) as connection, connection.cursor() as cur:
        tenant_id = locate_tenant(cur)
        released_live = count_active_nodes(cur, tenant_id, space_key, node_keys)

    # ------- 留痕: releases insert nodes; revisions are captured on mutation ----
    # node_revisions is written by a BEFORE UPDATE trigger, so a fresh create has
    # no snapshot yet.  Demonstrate traceability by mutating one released node:
    # the change must produce an immutable revision row while the node stays live.
    revision_snapshot_count = 0
    if node_keys:
        graph = GraphService(args.database_url)
        sample_key = sorted(node_keys)[0]
        graph.upsert_node(space_key, sample_key, sample_key.split(":", 1)[0], f"{sample_key} (mutated)")
        with psycopg2.connect(args.database_url) as connection, connection.cursor() as cur:
            tenant_id = locate_tenant(cur)
            cur.execute(
                """SELECT count(*) FROM graph.node_revisions r JOIN graph.nodes n ON n.id=r.node_id
                WHERE n.tenant_id=%s AND n.canonical_key = ANY(%s)""",
                (tenant_id, sorted(node_keys)),
            )
            revision_snapshot_count = int(cur.fetchone()[0])

    # ------------------------------------------------------------------- report
    report: dict[str, Any] = {
        "database": "audit_network_test",
        "documents": args.documents,
        "candidate_count": len(all_candidates),
        "conflict_count": len(routed.conflicts),
        "stageable_count": len(stageable),
        "staged_as_changeset": str(changeset_id) if changeset_id else None,
        "pre_approval_live_nodes": pre_live,
        "released_live_nodes": released_live,
        "revision_snapshot_count_after_mutation": revision_snapshot_count,
        "extract_ms": round(extraction_ms, 2),
        "staging_ms": round(staging_ms, 2),
        "release_ms": round(release_ms, 2),
        "per_document_insert_sec": round((per_doc[0]) / 1000, 4),
        "space_key": space_key,
    }
    print(json.dumps(report, ensure_ascii=False))

    # ---- quality gates ----------------------------------------------------------
    if changeset_id is None:
        raise SystemExit("no stageable candidates — synthetic documents must extract")
    if pre_live != 0:
        raise SystemExit("shadow ChangeSet leaked live nodes before approval")
    if released_live == 0:
        raise SystemExit("approval released no nodes — release cycle broken")
    if released_live != len(node_keys):
        raise SystemExit(f"released {released_live} nodes but expected {len(node_keys)}")
    if revision_snapshot_count < 1:
        raise SystemExit("no node_revisions captured on released-node mutation — 留痕 broken")
    if staging_ms > args.staging_budget_ms:
        raise SystemExit(f"staging exceeded budget: {staging_ms:.0f}ms > {args.staging_budget_ms}ms")
    if release_ms > args.release_budget_ms:
        raise SystemExit(f"release exceeded budget: {release_ms:.0f}ms > {args.release_budget_ms}ms")


if __name__ == "__main__":
    main()