from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.knowledge.ingest import ingest_directory
from packages.knowledge.retrieval import KnowledgeRetrievalService

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


class _Embedder:
    model = "test-1024"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0 if index == 0 else 0.0 for index in range(1024)] for _ in texts]


class _OtherEmbedder:
    """A different model key: chunks embedded elsewhere are not retrievable by it."""

    model = "other-768"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0 if index == 0 else 0.0 for index in range(1024)] for _ in texts]


def test_vector_counts_are_scoped_to_the_active_model(tmp_path: Path) -> None:
    """A chunk embedded by another model must not look indexed.

    Counting embeddings across every ``model_key`` used to report a corpus as
    fully vectorised whenever *any* model had rows — and let a leftover test
    model be presented as the corpus's embedding model.  Retrieval filters on
    ``model_key``, so the numbers have to as well.
    """

    source = tmp_path / f"scoped-{uuid4().hex}"
    source.mkdir()
    (source / "scoped.md").write_text("存货跌价准备需要按可变现净值重算。", encoding="utf-8")
    result = ingest_directory(source, database_url=DB)
    assert result.batch_id

    active = KnowledgeRetrievalService(DB, embedder=_Embedder())
    assert active.embed_pending(limit=10, batch_id=result.batch_id) == 1

    stats_active = active.stats()
    assert stats_active["embedding_model"] == "test-1024"
    assert stats_active["embedding_chunks"] >= 1
    assert stats_active["embedding_coverage"] > 0
    assert stats_active["vector_ready"] is True

    document = next(item for item in active.list_documents(limit=200) if item["title"] == "scoped")
    assert document["embedding_chunks"] == document["chunks"] >= 1
    assert document["embedding_status"] == "ready"

    other = KnowledgeRetrievalService(DB, embedder=_OtherEmbedder())
    stats_other = other.stats()
    assert stats_other["embedding_model"] == "other-768"
    assert stats_other["embedding_chunks"] == 0
    assert stats_other["embedding_coverage"] == 0.0
    assert stats_other["vector_ready"] is False
    assert "test-1024" in stats_other["embedding_models"], "stale rows must stay diagnosable"
    assert "other-768" not in stats_other["embedding_models"]

    foreign = next(item for item in other.list_documents(limit=200) if item["title"] == "scoped")
    assert foreign["embedding_chunks"] == 0
    assert foreign["embedding_status"] == "pending"
    assert foreign["chunks"] >= 1


def test_ingest_embed_search_and_management_views(tmp_path: Path) -> None:
    source = tmp_path / f"retrieval-{uuid4().hex}"
    source.mkdir()
    (source / "control.md").write_text("收入截止性测试需要核对期后发票和出库记录。", encoding="utf-8")
    result = ingest_directory(source, database_url=DB)
    assert result.batch_id

    service = KnowledgeRetrievalService(DB, embedder=_Embedder())
    embedded = service.embed_pending(limit=10, batch_id=result.batch_id)
    assert embedded == 1
    stats = service.stats()
    assert stats["embedding_chunks"] >= 1
    assert stats["embedding_model"] == "test-1024"
    documents = service.list_documents(limit=10)
    assert any(item["title"] == "control" and item["embedding_status"] == "ready" for item in documents)
    batches = service.list_batches(limit=10)
    assert any(item["id"] == result.batch_id for item in batches)
    keyword = service.search("收入截止性", mode="keyword", limit=5)
    assert any("期后发票" in item["content"] for item in keyword)
    vector = service.search("任意向量查询", mode="vector", limit=5)
    assert vector

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute("SELECT count(*) FROM semantic.chunk_embeddings WHERE model_key='test-1024'")
        assert cur.fetchone()[0] >= 1


def test_knowledge_management_and_keyword_search_api(tmp_path: Path) -> None:
    source = tmp_path / f"api-retrieval-{uuid4().hex}"
    source.mkdir()
    marker = f"截止性标记{uuid4().hex[:10]}"
    (source / "api.md").write_text(f"{marker} 需要核对资产负债表日前后凭证。", encoding="utf-8")
    ingest_directory(source, database_url=DB)
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                tenant_id,
                f"knowledge-api-{uuid4().hex}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["knowledge.read", "knowledge.search"]}}]),
            ),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    stats = client.get("/api/v1/knowledge/stats", headers=headers)
    documents = client.get("/api/v1/knowledge/documents", headers=headers)
    batches = client.get("/api/v1/knowledge/batches", headers=headers)
    search = client.post(
        "/api/v1/knowledge/search",
        headers=headers,
        json={"query": marker, "mode": "keyword", "limit": 5},
    )
    assert stats.status_code == documents.status_code == batches.status_code == search.status_code == 200
    assert stats.json()["documents"] >= 1
    assert any(item["title"] == "api" for item in documents.json()["items"])
    assert batches.json()["items"]
    assert marker in search.json()["items"][0]["content"]
    assert search.json()["degraded"] is False
