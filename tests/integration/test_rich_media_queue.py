from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
import pytest

from packages.knowledge.ingest import ingest_directory
from packages.knowledge.rich_media import ParsedDocument, RichMediaChunk, RichMediaParseError
from packages.knowledge.rich_media_service import (
    RichMediaQueueConflict,
    enqueue_rich_media_extraction,
    list_pending_rich_media,
    process_next_rich_media_job,
)

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


class _FakeAdapter:
    def __init__(self, parsed: ParsedDocument | None = None, error: Exception | None = None) -> None:
        self.parsed = parsed
        self.error = error
        self.calls = 0

    def extract(self, *, uri: str, sha256: str, size_bytes: int) -> ParsedDocument:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.parsed is not None
        assert self.parsed.input_sha256 == sha256
        return self.parsed


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


def _stage_pdf(tmp_path: Path) -> tuple[UUID, str]:
    source = tmp_path / f"queue-{uuid4().hex}.pdf"
    body = f"%PDF-1.4 queue fixture {uuid4().hex}".encode()
    source.write_bytes(body)
    ingested = ingest_directory(tmp_path, database_url=DB)
    tenant_id = _tenant_id()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute(
            """SELECT id FROM knowledge.ingest_files
            WHERE tenant_id=%s AND batch_id=%s AND relative_path=%s""",
            (tenant_id, ingested.batch_id, source.name),
        )
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0])), hashlib.sha256(body).hexdigest()


def test_rich_media_queue_claims_one_job_and_completes_page_cited_document(tmp_path: Path) -> None:
    ingest_file_id, sha256 = _stage_pdf(tmp_path)
    queued = enqueue_rich_media_extraction(
        DB,
        ingest_file_id,
        method="txt",
        idempotency_key=f"queue-{uuid4().hex}",
        trace_id=uuid4(),
    )
    replay = enqueue_rich_media_extraction(
        DB,
        ingest_file_id,
        method="txt",
        idempotency_key=queued.idempotency_key,
        trace_id=uuid4(),
    )
    assert replay.job_id == queued.job_id
    assert queued.status == "queued"
    with pytest.raises(RichMediaQueueConflict, match="already has an active"):
        enqueue_rich_media_extraction(
            DB, ingest_file_id, method="txt", idempotency_key=f"other-{uuid4().hex}", trace_id=uuid4()
        )

    adapter = _FakeAdapter(
        ParsedDocument(
            adapter_key="local.mineru",
            adapter_version="queue-test",
            input_sha256=sha256,
            method="txt",
            markdown="# Queue fixture",
            chunks=(RichMediaChunk("text", "queue evidence", 0, (1.0, 2.0, 3.0, 4.0), 1),),
            images=(),
            duration_ms=3,
        )
    )
    processed = process_next_rich_media_job(DB, adapter_factory=lambda _claim: adapter, job_id=queued.job_id)
    assert processed is not None
    assert processed.status == "completed"
    assert processed.chunks == 1
    assert adapter.calls == 1

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant_id()
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute("SELECT status,document_id FROM knowledge.ingest_files WHERE id=%s", (ingest_file_id,))
        status, document_id = cur.fetchone()
        assert status == "completed"
        cur.execute("SELECT metadata->>'page_idx' FROM semantic.chunks WHERE document_id=%s", (document_id,))
        assert cur.fetchone()[0] == "0"


def test_rich_media_queue_records_failure_then_requires_explicit_retry(tmp_path: Path) -> None:
    ingest_file_id, _sha256 = _stage_pdf(tmp_path)
    queued = enqueue_rich_media_extraction(
        DB, ingest_file_id, method="ocr", idempotency_key=f"failed-{uuid4().hex}", trace_id=uuid4()
    )
    failed = process_next_rich_media_job(
        DB,
        adapter_factory=lambda _claim: _FakeAdapter(error=RichMediaParseError("controlled parse failure")),
        job_id=queued.job_id,
    )
    assert failed is not None
    assert failed.status == "failed"
    assert failed.job_id == queued.job_id
    assert any(item["id"] == str(ingest_file_id) and item["status"] == "failed" for item in list_pending_rich_media(DB))

    retried = enqueue_rich_media_extraction(
        DB,
        ingest_file_id,
        method="ocr",
        idempotency_key=f"retry-{uuid4().hex}",
        trace_id=uuid4(),
        retry=True,
    )
    assert retried.status == "queued"
    assert retried.job_id != queued.job_id
