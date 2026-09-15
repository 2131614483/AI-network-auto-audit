"""Tenant-safe local embedding, knowledge browsing and hybrid retrieval."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg2

from packages.ai import default_embedder

SearchMode = Literal["keyword", "vector", "hybrid"]


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


UrlOpener = Callable[..., Any]


class OllamaEmbedder:
    """Small local-only Ollama adapter with a strict 1,024-dimensional contract."""

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
        dimensions: int = 1024,
        opener: UrlOpener = urlopen,
    ) -> None:
        self.model = model or os.getenv("OLLAMA_EMBED_MODEL") or "qwen3-embedding:0.6b"
        resolved_url = base_url or os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434"
        self.base_url = resolved_url.rstrip("/")
        self.timeout = timeout
        self.dimensions = dimensions
        self._opener = opener

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("embedding input must contain non-empty text")
        payload = json.dumps({"model": self.model, "input": texts}, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Ollama embedding unavailable") from exc
        raw_vectors = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(texts):
            raise ValueError("Ollama returned an invalid embedding batch")
        vectors: list[list[float]] = []
        for raw in raw_vectors:
            if not isinstance(raw, list) or len(raw) != self.dimensions:
                raise ValueError(f"embedding must contain exactly {self.dimensions} values")
            vector = [float(value) for value in raw]
            if any(not math.isfinite(value) for value in vector):
                raise ValueError("embedding contains a non-finite value")
            vectors.append(vector)
        return vectors


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def reciprocal_rank_fusion(
    keyword: list[dict[str, Any]], vector: list[dict[str, Any]], *, limit: int, constant: int = 60
) -> list[dict[str, Any]]:
    """Fuse independent rankings without pretending their raw scores are comparable."""
    indexed: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}
    sources: dict[str, set[str]] = {}
    for source_name, ranking in (("keyword", keyword), ("vector", vector)):
        for rank, item in enumerate(ranking, start=1):
            key = str(item["chunk_id"])
            indexed.setdefault(key, item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (constant + rank)
            sources.setdefault(key, set()).add(source_name)
    ordered = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
    return [
        {
            **indexed[key],
            "score": scores[key],
            "retrieval_mode": "hybrid",
            "matched_by": sorted(sources[key]),
        }
        for key in ordered
    ]


class KnowledgeRetrievalService:
    def __init__(
        self,
        database_url: str,
        tenant_slug: str = "local-dev",
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self.database_url = database_url
        self.tenant_slug = tenant_slug
        # Default to the unified AI gateway's embedder so the embedding model,
        # endpoint and dimension come from the same AI settings as chat.  With
        # an unconfigured environment this resolves to the local Ollama model
        # and behaves exactly as the previous hard-wired default did.
        self.embedder = embedder or default_embedder()

    def _tenant(self, cur: Any) -> Any:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {self.tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return tenant_id

    def embed_pending(self, *, limit: int = 32, batch_id: str | None = None) -> int:
        if not 1 <= limit <= 256:
            raise ValueError("limit must be between 1 and 256")
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = self._tenant(cur)
            params: list[Any] = [tenant_id]
            batch_clause = ""
            if batch_id:
                batch_clause = " AND ingest.batch_id=%s"
                params.append(batch_id)
            params.append(limit)
            cur.execute(
                """
                SELECT DISTINCT chunk.id,chunk.content
                FROM semantic.chunks chunk
                JOIN semantic.documents document ON document.id=chunk.document_id
                LEFT JOIN knowledge.ingest_files ingest ON ingest.document_id=document.id
                LEFT JOIN semantic.chunk_embeddings embedding
                  ON embedding.chunk_id=chunk.id AND embedding.model_key=%s
                WHERE chunk.tenant_id=%s
                  AND chunk.document_version=document.current_version
                  AND document.status <> 'retired'
                  AND embedding.chunk_id IS NULL
                """.replace("WHERE chunk.tenant_id=%s", f"WHERE chunk.tenant_id=%s{batch_clause}")
                + " ORDER BY chunk.id LIMIT %s",
                # SQL placeholder order is model, tenant, optional batch, limit.
                [self.embedder.model, *params],
            )
            rows = cur.fetchall()
        if not rows:
            return 0
        vectors = self.embedder.embed([str(row[1]) for row in rows])
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = self._tenant(cur)
            for row, vector in zip(rows, vectors, strict=True):
                cur.execute(
                    """INSERT INTO semantic.chunk_embeddings(chunk_id,model_key,embedding,tenant_id)
                    VALUES(%s,%s,%s::vector,%s)
                    ON CONFLICT(chunk_id) DO UPDATE SET model_key=EXCLUDED.model_key,
                    embedding=EXCLUDED.embedding,tenant_id=EXCLUDED.tenant_id,created_at=now()""",
                    (row[0], self.embedder.model, _vector_literal(vector), tenant_id),
                )
        return len(rows)

    def stats(self) -> dict[str, Any]:
        """Corpus and vector-index statistics.

        Vector counts are scoped to the **active embedder model** on purpose: a
        chunk embedded by a different model is not retrievable by :meth:`_vector`,
        which filters on ``model_key``.  Counting across every model (the earlier
        behaviour) made a corpus with a handful of usable vectors look fully
        indexed, and let a leftover test model be reported as *the* embedding
        model.  ``embedding_models`` keeps the per-model distribution visible so
        stale rows stay diagnosable instead of hidden.
        """

        model = self.embedder.model
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = self._tenant(cur)
            cur.execute(
                """SELECT count(DISTINCT document.id),count(DISTINCT chunk.id),
                count(DISTINCT embedding.chunk_id)
                FROM semantic.documents document
                LEFT JOIN semantic.chunks chunk ON chunk.document_id=document.id
                  AND chunk.document_version=document.current_version
                LEFT JOIN semantic.chunk_embeddings embedding ON embedding.chunk_id=chunk.id
                  AND embedding.model_key=%s
                WHERE document.tenant_id=%s AND document.status <> 'retired'""",
                (model, tenant_id),
            )
            stats_row = cur.fetchone()
            if stats_row is None:  # pragma: no cover - aggregate always returns one row
                raise RuntimeError("knowledge statistics query returned no row")
            documents, chunks, embeddings = stats_row
            cur.execute(
                """SELECT embedding.model_key,count(*)
                FROM semantic.chunk_embeddings embedding
                JOIN semantic.chunks chunk ON chunk.id=embedding.chunk_id
                JOIN semantic.documents document ON document.id=chunk.document_id
                WHERE embedding.tenant_id=%s AND document.status <> 'retired'
                GROUP BY embedding.model_key ORDER BY count(*) DESC""",
                (tenant_id,),
            )
            models = {str(key): int(count) for key, count in cur.fetchall()}
            cur.execute(
                """SELECT count(*) FILTER (WHERE status IN ('waiting_extractor','queued','processing')),
                count(*) FILTER (WHERE status='failed')
                FROM knowledge.ingest_files WHERE tenant_id=%s""",
                (tenant_id,),
            )
            file_row = cur.fetchone()
            if file_row is None:  # pragma: no cover - aggregate always returns one row
                raise RuntimeError("knowledge file statistics query returned no row")
            waiting, failed = file_row
        return {
            "documents": documents,
            "chunks": chunks,
            "embedding_chunks": embeddings,
            "embedding_model": model,
            "embedding_coverage": round(embeddings / chunks, 4) if chunks else 0.0,
            "embedding_models": models,
            "waiting_extractor": waiting,
            "failed_files": failed,
            "vector_ready": bool(embeddings > 0),
        }

    def list_documents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = self._tenant(cur)
            cur.execute(
                """SELECT document.id,document.title,document.source_uri,document.status,
                document.current_version,document.content_sha256,document.updated_at,
                count(chunk.id),count(embedding.chunk_id)
                FROM semantic.documents document
                LEFT JOIN semantic.chunks chunk ON chunk.document_id=document.id
                  AND chunk.document_version=document.current_version
                LEFT JOIN semantic.chunk_embeddings embedding ON embedding.chunk_id=chunk.id
                  AND embedding.model_key=%s
                WHERE document.tenant_id=%s AND document.status <> 'retired'
                GROUP BY document.id ORDER BY document.updated_at DESC LIMIT %s""",
                (self.embedder.model, tenant_id, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "id": str(row[0]), "title": row[1], "source_uri": row[2], "status": row[3],
                "version": row[4], "sha256": row[5], "updated_at": row[6], "chunks": row[7],
                "embedding_chunks": row[8],
                "embedding_status": "ready" if row[7] > 0 and row[7] == row[8] else "pending",
            }
            for row in rows
        ]

    def list_batches(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = self._tenant(cur)
            cur.execute(
                """SELECT id,source_uri,status,scanned_count,accepted_count,skipped_count,
                failed_count,deferred_count,created_at,finished_at
                FROM knowledge.ingest_batches WHERE tenant_id=%s
                ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "id": str(row[0]), "source_uri": row[1], "status": row[2], "scanned": row[3],
                "accepted": row[4], "skipped": row[5], "failed": row[6], "deferred": row[7],
                "created_at": row[8], "finished_at": row[9],
            }
            for row in rows
        ]

    def _keyword(self, query: str, limit: int) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = self._tenant(cur)
            cur.execute(
                """SELECT chunk.id,document.id,document.title,document.source_uri,chunk.ordinal,
                chunk.content,GREATEST(ts_rank_cd(chunk.search_vector,websearch_to_tsquery('simple',%s)),
                similarity(chunk.content,%s)) AS score
                FROM semantic.chunks chunk JOIN semantic.documents document ON document.id=chunk.document_id
                WHERE chunk.tenant_id=%s AND chunk.document_version=document.current_version
                AND document.status <> 'retired'
                AND (chunk.search_vector @@ websearch_to_tsquery('simple',%s)
                     OR chunk.content ILIKE '%%' || %s || '%%' OR similarity(chunk.content,%s)>0.05)
                ORDER BY score DESC,chunk.id LIMIT %s""",
                (query, query, tenant_id, query, query, query, limit),
            )
            rows = cur.fetchall()
        return [self._search_row(row, "keyword") for row in rows]

    def _vector(self, query: str, limit: int) -> list[dict[str, Any]]:
        query_vector = _vector_literal(self.embedder.embed([query])[0])
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = self._tenant(cur)
            cur.execute(
                """SELECT chunk.id,document.id,document.title,document.source_uri,chunk.ordinal,
                chunk.content,1-(embedding.embedding <=> %s::vector) AS score
                FROM semantic.chunk_embeddings embedding
                JOIN semantic.chunks chunk ON chunk.id=embedding.chunk_id
                JOIN semantic.documents document ON document.id=chunk.document_id
                WHERE embedding.tenant_id=%s AND embedding.model_key=%s
                  AND chunk.document_version=document.current_version
                  AND document.status <> 'retired'
                ORDER BY embedding.embedding <=> %s::vector LIMIT %s""",
                (query_vector, tenant_id, self.embedder.model, query_vector, limit),
            )
            rows = cur.fetchall()
        return [self._search_row(row, "vector") for row in rows]

    @staticmethod
    def _search_row(row: Any, mode: str) -> dict[str, Any]:
        return {
            "chunk_id": str(row[0]), "document_id": str(row[1]), "title": row[2],
            "source_uri": row[3], "ordinal": row[4], "content": row[5],
            "score": float(row[6]), "retrieval_mode": mode, "matched_by": [mode],
        }

    def search(self, query: str, *, mode: SearchMode = "hybrid", limit: int = 10) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        if mode == "keyword":
            return self._keyword(query, limit)
        if mode == "vector":
            return self._vector(query, limit)
        keyword = self._keyword(query, limit * 2)
        try:
            vector = self._vector(query, limit * 2)
        except RuntimeError:
            return keyword
        return reciprocal_rank_fusion(keyword, vector, limit=limit)
