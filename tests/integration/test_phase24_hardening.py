from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg2
import pytest

from packages.graph.service import GraphBudget, GraphService
from packages.knowledge.ingest import ingest_directory

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


@pytest.fixture()
def database() -> None:
    try:
        with psycopg2.connect(DB):
            return
    except psycopg2.Error as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"native PostgreSQL unavailable: {exc}")


def test_ingest_ledger_is_idempotent_and_versions_changed_content(tmp_path: Path, database: None) -> None:
    path = tmp_path / "memo.md"
    path.write_text("---\ntitle: First\n---\nfirst body", encoding="utf-8")
    key = f"phase24-{uuid.uuid4()}"
    first = ingest_directory(tmp_path, database_url=DB, idempotency_key=key)
    retry = ingest_directory(tmp_path, database_url=DB, idempotency_key=key)
    path.write_text("---\ntitle: Second\n---\nchanged body", encoding="utf-8")
    changed = ingest_directory(tmp_path, database_url=DB, idempotency_key=key)
    assert first.accepted == 1
    assert retry.skipped == 1
    assert changed.accepted == 1

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            "SELECT status,accepted_count,skipped_count FROM knowledge.ingest_batches WHERE id=%s",
            (changed.batch_id,),
        )
        assert cur.fetchone() == ("completed", 1, 0)
        cur.execute(
            "SELECT current_version,title FROM semantic.documents WHERE tenant_id=%s AND source_uri=%s",
            (tenant_id, first.files[0].source_uri),
        )
        assert cur.fetchone() == (2, "Second")
        cur.execute(
            """SELECT count(*) FROM semantic.chunks c JOIN semantic.documents d ON d.id=c.document_id
             WHERE d.tenant_id=%s AND d.source_uri=%s AND c.document_version=1""",
            (tenant_id, first.files[0].source_uri),
        )
        assert cur.fetchone()[0] == 1


def test_graph_database_rejects_cross_space_edges_and_marks_hop_truncation(database: None) -> None:
    service = GraphService(DB)
    suffix = uuid.uuid4().hex[:10]
    first_space = f"hardening-a-{suffix}"
    second_space = f"hardening-b-{suffix}"
    service.ensure_space(first_space, "L1", "Hardening A")
    service.ensure_space(second_space, "L1", "Hardening B")
    source = service.upsert_node(first_space, f"source:{suffix}", "test", "Source")
    target = service.upsert_node(first_space, f"target:{suffix}", "test", "Target")
    foreign = service.upsert_node(second_space, f"foreign:{suffix}", "test", "Foreign")
    service.upsert_edge(first_space, source, target, "links")
    result = service.bounded_neighbors(first_space, source, GraphBudget(max_nodes=10, max_edges=10, max_hops=1))
    assert result["partial"] is True
    with pytest.raises(psycopg2.Error):
        service.upsert_edge(first_space, source, foreign, "cross-space-forbidden")


def test_node_recycle_snapshot_is_server_backed(database: None) -> None:
    service = GraphService(DB)
    suffix = uuid.uuid4().hex[:10]
    space = f"hardening-trash-{suffix}"
    service.ensure_space(space, "L1", "Hardening Trash")
    node = service.upsert_node(space, f"trash:{suffix}", "test", "Stored Label", {"truth": 1})
    service.soft_delete_node(node, {"caller_note": "review"})
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            "SELECT id,snapshot FROM knowledge.recycle_bin WHERE tenant_id=%s AND entity_id=%s AND entity_kind='graph.node' ORDER BY deleted_at DESC LIMIT 1",
            (tenant_id, node),
        )
        recycle_id, snapshot = cur.fetchone()
        assert snapshot["canonical_key"] == f"trash:{suffix}"
        assert snapshot["label"] == "Stored Label"
    assert service.restore_node(recycle_id) == node
