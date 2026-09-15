from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2

from packages.knowledge.ingest import ingest_directory
from packages.knowledge.rich_media import ParsedDocument, RichMediaChunk
from packages.knowledge.rich_media_service import (
    extract_deferred_rich_media,
    list_pending_rich_media,
)

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


class _FakeAdapter:
    """Deterministic stand-in for MineruAdapter; records the resolved input."""

    def __init__(self, parsed: ParsedDocument) -> None:
        self._parsed = parsed
        self.calls: list[dict[str, object]] = []

    def extract(self, *, uri: str, sha256: str, size_bytes: int) -> ParsedDocument:
        self.calls.append({"uri": uri, "sha256": sha256, "size_bytes": size_bytes})
        return self._parsed


def _tenant_id() -> str:
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            return str(cur.fetchone()[0])


def _deferred_file(tenant_id: str, batch_id: str, relative_path: str) -> UUID:
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (tenant_id,))
            cur.execute(
                """SELECT id FROM knowledge.ingest_files
                WHERE tenant_id=%s AND batch_id=%s AND relative_path=%s
                  AND status='waiting_extractor' AND error_code='local.mineru'
                LIMIT 1""",
                (tenant_id, batch_id, relative_path),
            )
            row = cur.fetchone()
            assert row is not None
            return UUID(str(row[0]))


def test_extract_deferred_rich_media_writes_page_cited_chunks(tmp_path: Path) -> None:
    """The service closes the loop: pending PDF → (fake) parse → page-cited chunks."""

    source = tmp_path / "probe.pdf"
    content = f"%PDF-1.4 fake content for the service test {uuid4().hex}".encode()
    source.write_bytes(content)
    ingested = ingest_directory(tmp_path, database_url=DB)
    assert ingested.batch_id
    assert ingested.deferred == 1
    sha256 = hashlib.sha256(content).hexdigest()
    tenant_id = _tenant_id()
    ingest_file_id = _deferred_file(tenant_id, ingested.batch_id, "probe.pdf")

    parsed = ParsedDocument(
        adapter_key="local.mineru",
        adapter_version="test-2.0",
        input_sha256=sha256,
        method="txt",
        markdown="# Probe\n\nbody",
        chunks=(
            RichMediaChunk("text", "first block", 0, (1.0, 2.0, 3.0, 4.0), 1),
            RichMediaChunk("text", "second block", 1, None, None),
        ),
        images=(),
        duration_ms=12,
    )
    fake = _FakeAdapter(parsed)
    result = extract_deferred_rich_media(DB, ingest_file_id, adapter=fake, tenant_slug="local-dev")  # type: ignore[arg-type]

    assert result.chunks == 2
    assert result.pages == 2
    assert result.adapter_version == "test-2.0"
    assert fake.calls and fake.calls[0]["uri"] == source.as_uri()
    assert fake.calls[0]["sha256"] == sha256

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (tenant_id,))
            cur.execute(
                "SELECT status,document_id FROM knowledge.ingest_files WHERE tenant_id=%s AND id=%s",
                (tenant_id, ingest_file_id),
            )
            status, document_id = cur.fetchone()
            assert status == "completed"
            cur.execute(
                """SELECT ordinal,content,metadata->>'page_idx',metadata->>'type',metadata->>'adapter_key',metadata->>'input_sha256'
                FROM semantic.chunks WHERE tenant_id=%s AND document_id=%s ORDER BY ordinal""",
                (tenant_id, document_id),
            )
            chunks = cur.fetchall()
            assert len(chunks) == 2
            assert chunks[0][1] == "first block"
            assert chunks[0][2] == "0"
            assert chunks[0][3] == "text"
            assert chunks[0][4] == "local.mineru"
            assert chunks[0][5] == sha256
            assert chunks[1][1] == "second block"
            assert chunks[1][2] == "1"


def test_list_pending_rich_media_reports_waiting_files(tmp_path: Path) -> None:
    source = tmp_path / f"pending-{uuid4().hex}.pdf"
    content = f"%PDF-1.4 {uuid4().hex}".encode()
    source.write_bytes(content)
    ingested = ingest_directory(tmp_path, database_url=DB)
    assert ingested.batch_id
    assert ingested.deferred == 1
    pending = list_pending_rich_media(DB, tenant_slug="local-dev")
    assert any(item["relative_path"] == source.name for item in pending)
