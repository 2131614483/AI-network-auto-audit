from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from apps.worker.local import process_local_rich_media_once
from packages.knowledge.ingest import ingest_directory
from packages.knowledge.retrieval import KnowledgeRetrievalService

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")
MINERU_EXE = os.getenv("MINERU_EXECUTABLE", "")


def _minimal_pdf() -> bytes:
    content = b"BT /F1 18 Tf 72 720 Td (AuditNetworkQueueE2E) Tj ET"
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        b"4 0 obj\n<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objects:
        offsets.append(len(body))
        body += obj
    xref = b"xref\n0 6\n0000000000 65535 f \n" + b"".join(
        f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets
    )
    return bytes(body) + xref + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(len(body)).encode() + b"\n%%EOF\n"


def _require_mineru() -> None:
    if not os.getenv("AUDIT_NETWORK_RUN_MINERU_TESTS"):
        pytest.skip("set AUDIT_NETWORK_RUN_MINERU_TESTS=1 to run real MinerU queue integration")
    if not MINERU_EXE or not Path(MINERU_EXE).is_file():
        pytest.skip(f"MinerU executable not found: {MINERU_EXE or '<MINERU_EXECUTABLE unset>'}")


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


def test_real_mineru_worker_queue_writes_retrievable_page_cited_document(tmp_path: Path) -> None:
    """Opt-in native E2E: staged PDF → queued job → Worker → retrieval."""

    _require_mineru()
    source = tmp_path / "queue-e2e.pdf"
    source.write_bytes(_minimal_pdf())
    ingested = ingest_directory(tmp_path, database_url=DB)
    tenant_id = _tenant_id()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                tenant_id,
                f"mineru-e2e-policy-{uuid4().hex}",
                Json([{ "rule_id": str(uuid4()), "effect": "allow", "match": {
                    "capabilities": ["knowledge.extract.rich_media"], "risk_classes": ["high"], "side_effects": ["write_data"]
                }}]),
            ),
        )
        cur.execute(
            "SELECT id FROM knowledge.ingest_files WHERE tenant_id=%s AND batch_id=%s AND relative_path=%s",
            (tenant_id, ingested.batch_id, source.name),
        )
        ingest_file_id = UUID(str(cur.fetchone()[0]))
    queued_response = TestClient(create_app(Settings(database_url=DB))).post(
        "/api/v1/knowledge/rich-media/extract",
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(uuid4()),
            "Idempotency-Key": f"mineru-queue-{uuid4().hex}",
        },
        json={"ingest_file_id": str(ingest_file_id), "method": "txt"},
    )
    assert queued_response.status_code == 200, queued_response.text
    queued = queued_response.json()
    assert queued["status"] == "queued"
    assert process_local_rich_media_once(DB, job_id=UUID(queued["job_id"]))
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute(
            "SELECT status,page_count,chunk_count FROM knowledge.rich_media_jobs WHERE id=%s",
            (UUID(queued["job_id"]),),
        )
        status, pages, chunks = cur.fetchone()
    assert status == "completed"
    assert pages == 1
    assert chunks > 0
    hits = KnowledgeRetrievalService(DB).search("AuditNetworkQueueE2E", mode="keyword", limit=5)
    assert any("AuditNetworkQueueE2E" in hit["content"] for hit in hits)
