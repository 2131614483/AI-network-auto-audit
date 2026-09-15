"""Policy-gated orchestration: deferred PDF/image → MinerU → page-cited chunks.

Phase 2 stages rich files (PDF/images) as ``waiting_extractor`` ingest rows
bound to a local ``local.mineru`` seam.  This module closes that loop: it
resolves the staged artifact, runs the isolated ``MineruAdapter``, then writes
the parsed content blocks into ``semantic.chunks`` with page citations kept in
chunk metadata.  Like the Phase 2 text ingester, writes are staged (never hard
deleted) and the document remains governed by the existing retire/restore
lifecycle.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import Json, register_uuid

from packages.knowledge.rich_media import ParsedDocument, RichMediaParseError

register_uuid()  # type: ignore[no-untyped-call]


@dataclass(frozen=True, slots=True)
class RichMediaExtractionResult:
    ingest_file_id: UUID
    document_id: UUID
    chunks: int
    pages: int
    adapter_version: str
    duration_ms: int


class RichMediaAdapter(Protocol):
    """The intentionally tiny surface a controlled local extractor exposes."""

    def extract(self, *, uri: str, sha256: str, size_bytes: int) -> ParsedDocument: ...


class RichMediaQueueConflict(RuntimeError):
    """A request conflicts with an existing durable rich-media job."""


@dataclass(frozen=True, slots=True)
class RichMediaQueueResult:
    job_id: UUID
    ingest_file_id: UUID
    status: str
    attempt_count: int
    method: str
    idempotency_key: str
    document_id: UUID | None = None
    chunks: int | None = None
    pages: int | None = None
    adapter_version: str | None = None
    duration_ms: int | None = None
    error_detail: str | None = None


@dataclass(frozen=True, slots=True)
class RichMediaJobClaim:
    """A short-lived worker lease.  It is never exposed through the API."""

    job_id: UUID
    ingest_file_id: UUID
    lease_token: UUID
    path: Path
    sha256: str
    size_bytes: int
    source_uri: str
    title: str
    method: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class _DeferredFile:
    path: Path
    sha256: str
    size_bytes: int
    source_uri: str
    title: str


def _tenant(cur: psycopg2.extensions.cursor, tenant_slug: str) -> UUID:
    cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"tenant not found: {tenant_slug}")
    tenant_id = UUID(str(row[0]))
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
    return tenant_id


def _resolve_deferred(
    cur: psycopg2.extensions.cursor,
    tenant_id: UUID,
    ingest_file_id: UUID,
    *,
    expected_status: str = "waiting_extractor",
) -> _DeferredFile:
    cur.execute(
        """
        SELECT file.source_uri,file.sha256,file.size_bytes,file.relative_path,blob.storage_uri
        FROM knowledge.ingest_files file
        JOIN artifact.artifacts artifact ON artifact.id=file.source_artifact_id
        JOIN artifact.blobs blob ON blob.sha256=artifact.blob_sha256
        WHERE file.tenant_id=%s AND file.id=%s
          AND file.status=%s AND file.error_code='local.mineru'
        """,
        (tenant_id, ingest_file_id, expected_status),
    )
    row = cur.fetchone()
    if row is None:
        raise RichMediaParseError("rich-media ingest file is not pending local.mineru extraction")
    source_uri, sha256, size_bytes, relative_path, storage_uri = row
    path = _storage_path(storage_uri)
    title = Path(str(relative_path)).stem if relative_path else Path(path).stem
    return _DeferredFile(path, str(sha256), int(size_bytes), str(source_uri), title)


def _storage_path(storage_uri: str) -> Path:
    """Reconstruct a local path from a ``file://`` storage URI.

    ``ingest.py`` writes ``f"file://{path}"`` which on Windows yields
    ``file://C:\\...`` (a form ``urlparse`` would split at the drive colon), so
    the prefix is stripped textually rather than parsed as an RFC URI.
    """
    if not storage_uri.lower().startswith("file://"):
        raise RichMediaParseError("rich-media artifact storage is not a local file")
    raw = unquote(storage_uri[len("file://") :])
    if raw.startswith("/") and len(raw) >= 3 and raw[2] == ":":
        raw = raw[1:]
    path = Path(raw)
    if not path.is_absolute():
        raise RichMediaParseError("rich-media artifact storage path is not absolute")
    return path


def _upsert_document(cur: psycopg2.extensions.cursor, tenant_id: UUID, deferred: _DeferredFile, artifact_id: UUID) -> tuple[UUID, int]:
    cur.execute(
        "SELECT id,current_version,content_sha256 FROM semantic.documents WHERE tenant_id=%s AND source_uri=%s FOR UPDATE",
        (tenant_id, deferred.source_uri),
    )
    existing = cur.fetchone()
    if existing is None:
        cur.execute(
            """INSERT INTO semantic.documents
            (tenant_id,source_uri,title,status,current_version,content_sha256,source_artifact_id)
            VALUES(%s,%s,%s,'staged',1,%s,%s) RETURNING id""",
            (tenant_id, deferred.source_uri, deferred.title, deferred.sha256, artifact_id),
        )
        row = cur.fetchone()
        if row is None:
            raise RichMediaParseError("rich-media document insert returned no id")
        return UUID(str(row[0])), 1
    if existing[2] == deferred.sha256:
        return UUID(str(existing[0])), int(existing[1])
    version = int(existing[1]) + 1
    cur.execute(
        """UPDATE semantic.documents SET title=%s,current_version=%s,content_sha256=%s,
        source_artifact_id=%s,updated_at=now() WHERE tenant_id=%s AND id=%s""",
        (deferred.title, version, deferred.sha256, artifact_id, tenant_id, existing[0]),
    )
    return UUID(str(existing[0])), version


def _write_chunks(
    cur: psycopg2.extensions.cursor,
    tenant_id: UUID,
    document_id: UUID,
    document_version: int,
    parsed: ParsedDocument,
) -> int:
    written = 0
    for ordinal, chunk in enumerate(parsed.chunks):
        if not chunk.text.strip():
            continue
        metadata: dict[str, object] = {
            "type": chunk.type,
            "page_idx": chunk.page_idx,
            "adapter_key": parsed.adapter_key,
            "input_sha256": parsed.input_sha256,
        }
        if chunk.bbox is not None:
            metadata["bbox"] = list(chunk.bbox)
        if chunk.text_level is not None:
            metadata["text_level"] = chunk.text_level
        cur.execute(
            """INSERT INTO semantic.chunks
            (tenant_id,document_id,document_version,ordinal,content,token_count,metadata)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (tenant_id, document_id, document_version, ordinal, chunk.text, len(chunk.text), Json(metadata)),
        )
        written += 1
    return written


def _request_hash(ingest_file_id: UUID, method: str, retry: bool) -> str:
    payload = json.dumps(
        {"ingest_file_id": str(ingest_file_id), "method": method, "retry": retry},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _queue_result(row: tuple[Any, ...]) -> RichMediaQueueResult:
    return RichMediaQueueResult(
        job_id=UUID(str(row[0])),
        ingest_file_id=UUID(str(row[1])),
        status=str(row[2]),
        attempt_count=int(row[3]),
        method=str(row[4]),
        idempotency_key=str(row[5]),
        document_id=UUID(str(row[6])) if row[6] is not None else None,
        chunks=int(row[7]) if row[7] is not None else None,
        pages=int(row[8]) if row[8] is not None else None,
        adapter_version=str(row[9]) if row[9] is not None else None,
        duration_ms=int(row[10]) if row[10] is not None else None,
        error_detail=str(row[11]) if row[11] is not None else None,
    )


def _job_row(cur: psycopg2.extensions.cursor, tenant_id: UUID, job_id: UUID) -> RichMediaQueueResult:
    cur.execute(
        """SELECT id,ingest_file_id,status,attempt_count,method,idempotency_key,document_id,
        chunk_count,page_count,adapter_version,duration_ms,error_detail
        FROM knowledge.rich_media_jobs WHERE tenant_id=%s AND id=%s""",
        (tenant_id, job_id),
    )
    row = cur.fetchone()
    if row is None:
        raise RichMediaQueueConflict("rich-media job is unavailable")
    return _queue_result(row)


def enqueue_rich_media_extraction(
    database_url: str,
    ingest_file_id: UUID,
    *,
    method: str,
    idempotency_key: str,
    trace_id: UUID,
    tenant_slug: str = "local-dev",
    retry: bool = False,
) -> RichMediaQueueResult:
    """Persist a policy-authorized request without running MinerU in the API process.

    The unique idempotency envelope replays the original job.  A distinct
    request cannot attach a second active GPU-consuming job to the same input.
    Failed input is never retried implicitly: callers must set ``retry=True``
    under a new idempotency key after a new gateway decision.
    """

    if method not in {"auto", "txt", "ocr"}:
        raise ValueError("method must be one of auto, txt, ocr")
    if not idempotency_key.strip():
        raise ValueError("Idempotency-Key is required")
    request_hash = _request_hash(ingest_file_id, method, retry)
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            tenant_id = _tenant(cur, tenant_slug)
            cur.execute(
                """SELECT id,ingest_file_id,status,attempt_count,method,idempotency_key,document_id,
                chunk_count,page_count,adapter_version,duration_ms,error_detail,request_hash
                FROM knowledge.rich_media_jobs
                WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE""",
                (tenant_id, idempotency_key),
            )
            previous = cur.fetchone()
            if previous is not None:
                if str(previous[12]) != request_hash:
                    raise RichMediaQueueConflict("Idempotency-Key was already used with different rich-media request")
                return _queue_result(previous[:12])

            cur.execute(
                "SELECT status,error_code FROM knowledge.ingest_files WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (tenant_id, ingest_file_id),
            )
            source = cur.fetchone()
            if source is None:
                raise RichMediaParseError("rich-media ingest file is not pending local.mineru extraction")
            status, error_code = str(source[0]), source[1]
            can_enqueue = status == "waiting_extractor" and error_code == "local.mineru"
            can_retry = retry and status == "failed" and error_code == "local.mineru.failed"
            if not can_enqueue and not can_retry:
                if status in {"queued", "processing"}:
                    raise RichMediaQueueConflict("rich-media ingest file already has an active extraction job")
                raise RichMediaParseError("rich-media ingest file is not pending local.mineru extraction")

            cur.execute(
                """INSERT INTO knowledge.rich_media_jobs
                (tenant_id,ingest_file_id,idempotency_key,request_hash,method,trace_id,status)
                VALUES(%s,%s,%s,%s,%s,%s,'queued')
                ON CONFLICT (tenant_id,ingest_file_id) WHERE status IN ('queued','processing') DO NOTHING
                RETURNING id""",
                (tenant_id, ingest_file_id, idempotency_key, request_hash, method, trace_id),
            )
            inserted = cur.fetchone()
            if inserted is None:
                raise RichMediaQueueConflict("rich-media ingest file already has an active extraction job")
            job_id = UUID(str(inserted[0]))
            cur.execute(
                """UPDATE knowledge.ingest_files SET status='queued',error_code='local.mineru',
                error_detail=NULL,finished_at=NULL WHERE tenant_id=%s AND id=%s""",
                (tenant_id, ingest_file_id),
            )
            return _job_row(cur, tenant_id, job_id)


def _claim_next_rich_media_job(
    database_url: str,
    *,
    tenant_slug: str,
    lease_seconds: int,
    job_id: UUID | None = None,
) -> RichMediaJobClaim | None:
    if not 30 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 30 and 3600")
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            tenant_id = _tenant(cur, tenant_slug)
            cur.execute(
                """SELECT job.id,job.ingest_file_id,job.method,job.attempt_count,
                file.source_uri,file.sha256,file.size_bytes,file.relative_path,blob.storage_uri
                FROM knowledge.rich_media_jobs job
                JOIN knowledge.ingest_files file ON file.id=job.ingest_file_id AND file.tenant_id=job.tenant_id
                JOIN artifact.artifacts artifact ON artifact.id=file.source_artifact_id AND artifact.tenant_id=file.tenant_id
                JOIN artifact.blobs blob ON blob.sha256=artifact.blob_sha256
                WHERE job.tenant_id=%s
                  AND (%s::uuid IS NULL OR job.id=%s)
                  AND (job.status='queued' OR (job.status='processing' AND job.lease_expires_at < now()))
                ORDER BY job.created_at,job.id
                FOR UPDATE OF job,file SKIP LOCKED LIMIT 1""",
                (tenant_id, job_id, job_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            job_id, ingest_file_id, method, attempts, source_uri, sha256, size_bytes, relative_path, storage_uri = row
            lease_token = UUID(str(uuid4()))
            cur.execute(
                """UPDATE knowledge.rich_media_jobs
                SET status='processing',attempt_count=attempt_count+1,lease_token=%s,
                    lease_expires_at=now() + (%s * interval '1 second'),started_at=now(),
                    error_code=NULL,error_detail=NULL,updated_at=now()
                WHERE tenant_id=%s AND id=%s""",
                (lease_token, lease_seconds, tenant_id, job_id),
            )
            cur.execute(
                """UPDATE knowledge.ingest_files SET status='processing',error_code='local.mineru',
                error_detail=NULL,finished_at=NULL WHERE tenant_id=%s AND id=%s""",
                (tenant_id, ingest_file_id),
            )
            path = _storage_path(str(storage_uri))
            title = Path(str(relative_path)).stem if relative_path else Path(path).stem
            return RichMediaJobClaim(
                job_id=UUID(str(job_id)),
                ingest_file_id=UUID(str(ingest_file_id)),
                lease_token=lease_token,
                path=path,
                sha256=str(sha256),
                size_bytes=int(size_bytes),
                source_uri=str(source_uri),
                title=title,
                method=str(method),
                attempt_count=int(attempts) + 1,
            )


def _lease_is_current(cur: psycopg2.extensions.cursor, tenant_id: UUID, claim: RichMediaJobClaim) -> None:
    cur.execute(
        """SELECT 1 FROM knowledge.rich_media_jobs
        WHERE tenant_id=%s AND id=%s AND status='processing' AND lease_token=%s
          AND lease_expires_at >= now() FOR UPDATE""",
        (tenant_id, claim.job_id, claim.lease_token),
    )
    if cur.fetchone() is None:
        raise RichMediaQueueConflict("rich-media worker lease is no longer active")


def _complete_claim(
    database_url: str,
    claim: RichMediaJobClaim,
    parsed: ParsedDocument,
    *,
    tenant_slug: str,
) -> RichMediaQueueResult:
    if parsed.input_sha256.lower() != claim.sha256.lower():
        raise RichMediaParseError("MinerU result sha256 does not match claimed artifact")
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            tenant_id = _tenant(cur, tenant_slug)
            _lease_is_current(cur, tenant_id, claim)
            cur.execute(
                "SELECT id FROM artifact.artifacts WHERE tenant_id=%s AND blob_sha256=%s ORDER BY created_at LIMIT 1",
                (tenant_id, claim.sha256),
            )
            artifact_row = cur.fetchone()
            if artifact_row is None:
                raise RichMediaParseError("rich-media artifact record is missing")
            deferred = _DeferredFile(claim.path, claim.sha256, claim.size_bytes, claim.source_uri, claim.title)
            document_id, document_version = _upsert_document(cur, tenant_id, deferred, UUID(str(artifact_row[0])))
            written = _write_chunks(cur, tenant_id, document_id, document_version, parsed)
            pages = len({chunk.page_idx for chunk in parsed.chunks})
            cur.execute(
                """UPDATE knowledge.ingest_files SET status='completed',document_id=%s,finished_at=now(),
                error_code=NULL,error_detail=NULL WHERE tenant_id=%s AND id=%s AND status='processing'""",
                (document_id, tenant_id, claim.ingest_file_id),
            )
            if cur.rowcount != 1:
                raise RichMediaQueueConflict("rich-media ingest file changed while worker was running")
            cur.execute(
                """UPDATE knowledge.rich_media_jobs SET status='completed',lease_token=NULL,
                lease_expires_at=NULL,adapter_key=%s,adapter_version=%s,document_id=%s,chunk_count=%s,
                page_count=%s,duration_ms=%s,error_code=NULL,error_detail=NULL,finished_at=now(),updated_at=now()
                WHERE tenant_id=%s AND id=%s AND status='processing' AND lease_token=%s""",
                (
                    parsed.adapter_key, parsed.adapter_version, document_id, written, pages, parsed.duration_ms,
                    tenant_id, claim.job_id, claim.lease_token,
                ),
            )
            if cur.rowcount != 1:
                raise RichMediaQueueConflict("rich-media worker lease is no longer active")
            return _job_row(cur, tenant_id, claim.job_id)


def _safe_failure_detail(exc: Exception) -> str:
    detail = " ".join(str(exc).split()) or "local MinerU processing failed"
    return detail[:1000]


def _fail_claim(
    database_url: str,
    claim: RichMediaJobClaim,
    exc: Exception,
    *,
    tenant_slug: str,
) -> RichMediaQueueResult:
    detail = _safe_failure_detail(exc)
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            tenant_id = _tenant(cur, tenant_slug)
            _lease_is_current(cur, tenant_id, claim)
            cur.execute(
                """UPDATE knowledge.ingest_files SET status='failed',error_code='local.mineru.failed',
                error_detail=%s,finished_at=now() WHERE tenant_id=%s AND id=%s AND status='processing'""",
                (detail, tenant_id, claim.ingest_file_id),
            )
            cur.execute(
                """UPDATE knowledge.rich_media_jobs SET status='failed',lease_token=NULL,lease_expires_at=NULL,
                error_code='local.mineru.failed',error_detail=%s,finished_at=now(),updated_at=now()
                WHERE tenant_id=%s AND id=%s AND status='processing' AND lease_token=%s""",
                (detail, tenant_id, claim.job_id, claim.lease_token),
            )
            if cur.rowcount != 1:
                raise RichMediaQueueConflict("rich-media worker lease is no longer active")
            return _job_row(cur, tenant_id, claim.job_id)


def process_next_rich_media_job(
    database_url: str,
    *,
    adapter_factory: Callable[[RichMediaJobClaim], RichMediaAdapter],
    tenant_slug: str = "local-dev",
    lease_seconds: int = 960,
    job_id: UUID | None = None,
) -> RichMediaQueueResult | None:
    """Claim at most one job and run it outside database transactions.

    A local Worker calls this with a single consumer, making GPU use explicit
    and bounded.  The database lease additionally prevents duplicate work if a
    second local worker starts or a previous worker dies mid-parse.
    """

    claim = _claim_next_rich_media_job(
        database_url, tenant_slug=tenant_slug, lease_seconds=lease_seconds, job_id=job_id
    )
    if claim is None:
        return None
    try:
        parsed = adapter_factory(claim).extract(
            uri=claim.path.as_uri(), sha256=claim.sha256, size_bytes=claim.size_bytes
        )
        return _complete_claim(database_url, claim, parsed, tenant_slug=tenant_slug)
    except RichMediaQueueConflict:
        # A reclaimed lease belongs to another worker now.  Never overwrite its
        # state with a stale worker's failure record.
        return None
    except RichMediaParseError as exc:
        return _fail_claim(database_url, claim, exc, tenant_slug=tenant_slug)
    except Exception as exc:  # pragma: no cover - protects the durable worker boundary
        return _fail_claim(database_url, claim, exc, tenant_slug=tenant_slug)


def extract_deferred_rich_media(
    database_url: str,
    ingest_file_id: UUID,
    *,
    adapter: RichMediaAdapter,
    tenant_slug: str = "local-dev",
) -> RichMediaExtractionResult:
    """Run MinerU on one pending rich-media ingest file and store page-cited chunks.

    The external subprocess runs between two short transactions so no database
    lock is held while MinerU loads models and parses the document.
    """

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            tenant_id = _tenant(cur, tenant_slug)
            deferred = _resolve_deferred(cur, tenant_id, ingest_file_id)

    parsed = adapter.extract(uri=deferred.path.as_uri(), sha256=deferred.sha256, size_bytes=deferred.size_bytes)

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            tenant_id = _tenant(cur, tenant_slug)
            cur.execute(
                "SELECT id FROM artifact.artifacts WHERE tenant_id=%s AND blob_sha256=%s ORDER BY created_at LIMIT 1",
                (tenant_id, deferred.sha256),
            )
            artifact_row = cur.fetchone()
            if artifact_row is None:
                raise RichMediaParseError("rich-media artifact record is missing")
            artifact_id = UUID(str(artifact_row[0]))
            document_id, document_version = _upsert_document(cur, tenant_id, deferred, artifact_id)
            written = _write_chunks(cur, tenant_id, document_id, document_version, parsed)
            pages = len({chunk.page_idx for chunk in parsed.chunks})
            cur.execute(
                """UPDATE knowledge.ingest_files SET status='completed',document_id=%s,
                finished_at=now(),error_detail=NULL
                WHERE tenant_id=%s AND id=%s AND status='waiting_extractor'""",
                (document_id, tenant_id, ingest_file_id),
            )
            if cur.rowcount != 1:
                raise RichMediaParseError("rich-media ingest file did not complete")
    return RichMediaExtractionResult(
        ingest_file_id=ingest_file_id,
        document_id=document_id,
        chunks=written,
        pages=pages,
        adapter_version=parsed.adapter_version,
        duration_ms=parsed.duration_ms,
    )


def list_pending_rich_media(
    database_url: str,
    *,
    tenant_slug: str = "local-dev",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List waiting, queued, processing and failed MinerU files for the desktop queue."""

    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cur:
            tenant_id = _tenant(cur, tenant_slug)
            cur.execute(
                """SELECT id,relative_path,source_uri,mime_type,size_bytes,status,error_detail,created_at
                FROM knowledge.ingest_files
                WHERE tenant_id=%s
                  AND status IN ('waiting_extractor','queued','processing','failed')
                  AND error_code IN ('local.mineru','local.mineru.failed')
                ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, limit),
            )
            rows = cur.fetchall()
    return [
        {
            "id": str(row[0]),
            "relative_path": row[1],
            "source_uri": row[2],
            "mime_type": row[3],
            "size_bytes": row[4],
            "status": row[5],
            "error_detail": row[6],
            "created_at": row[7].isoformat() if row[7] is not None else None,
        }
        for row in rows
    ]
