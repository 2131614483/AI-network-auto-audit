"""Tenant-safe document retirement and restoration for the knowledge library.

The service deliberately never deletes semantic rows.  A retire operation
captures the document identity and current visible state in the existing
recycle bin, then removes it from active library views and retrieval.  Restore
reverses only that visibility transition for the same tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg2
from psycopg2.extras import Json


@dataclass(frozen=True, slots=True)
class DocumentRecycleResult:
    document_id: UUID
    recycle_bin_id: UUID
    status: str


class KnowledgeLibraryService:
    """Manage visible knowledge documents without hard deletion."""

    def __init__(self, database_url: str, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.tenant_slug = tenant_slug

    def _tenant(self, cur: Any) -> UUID:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {self.tenant_slug}")
        tenant_id = UUID(str(row[0]))
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return tenant_id

    def retire_document(self, document_id: UUID, *, reason: str) -> DocumentRecycleResult:
        """Soft-delete one document and preserve a state snapshot for recovery."""

        if not reason.strip():
            raise ValueError("retire reason is required")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """
                    SELECT id,title,source_uri,status,current_version,content_sha256,source_artifact_id
                    FROM semantic.documents
                    WHERE tenant_id=%s AND id=%s
                    FOR UPDATE
                    """,
                    (tenant_id, document_id),
                )
                document = cur.fetchone()
                if document is None:
                    raise ValueError("knowledge document not found")
                if document[3] == "retired":
                    cur.execute(
                        """
                        SELECT id FROM knowledge.recycle_bin
                        WHERE tenant_id=%s AND entity_kind='semantic.document'
                          AND entity_id=%s AND restored_at IS NULL
                        ORDER BY deleted_at DESC,id DESC LIMIT 1
                        """,
                        (tenant_id, document_id),
                    )
                    existing = cur.fetchone()
                    if existing is None:
                        raise RuntimeError("retired document has no active recycle-bin record")
                    return DocumentRecycleResult(document_id, UUID(str(existing[0])), "retired")
                snapshot = {
                    "title": document[1],
                    "source_uri": document[2],
                    "status": document[3],
                    "current_version": document[4],
                    "content_sha256": document[5],
                    "source_artifact_id": str(document[6]) if document[6] is not None else None,
                    "reason": reason.strip(),
                }
                cur.execute(
                    """
                    INSERT INTO knowledge.recycle_bin(tenant_id,entity_kind,entity_id,snapshot)
                    VALUES(%s,'semantic.document',%s,%s) RETURNING id
                    """,
                    (tenant_id, document_id, Json(snapshot)),
                )
                recycle = cur.fetchone()
                if recycle is None:
                    raise RuntimeError("knowledge recycle-bin insert returned no id")
                cur.execute(
                    "UPDATE semantic.documents SET status='retired',updated_at=now() WHERE tenant_id=%s AND id=%s",
                    (tenant_id, document_id),
                )
                if cur.rowcount != 1:  # pragma: no cover - row is locked above
                    raise RuntimeError("knowledge document retirement did not update a row")
                return DocumentRecycleResult(document_id, UUID(str(recycle[0])), "retired")

    def restore_document(self, recycle_bin_id: UUID, *, reason: str) -> DocumentRecycleResult:
        """Restore a document's prior non-retired status from its own snapshot."""

        if not reason.strip():
            raise ValueError("restore reason is required")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """
                    SELECT entity_id,snapshot,restored_at FROM knowledge.recycle_bin
                    WHERE tenant_id=%s AND id=%s AND entity_kind='semantic.document'
                    FOR UPDATE
                    """,
                    (tenant_id, recycle_bin_id),
                )
                recycle = cur.fetchone()
                if recycle is None:
                    raise ValueError("knowledge recycle-bin entry not found")
                document_id = UUID(str(recycle[0]))
                snapshot = recycle[1]
                if not isinstance(snapshot, dict):
                    raise RuntimeError("knowledge recycle-bin snapshot is invalid")
                prior_status = snapshot.get("status")
                if not isinstance(prior_status, str) or not prior_status or prior_status == "retired":
                    raise RuntimeError("knowledge recycle-bin snapshot has no restorable status")
                cur.execute(
                    "UPDATE semantic.documents SET status=%s,updated_at=now() WHERE tenant_id=%s AND id=%s",
                    (prior_status, tenant_id, document_id),
                )
                if cur.rowcount != 1:
                    raise ValueError("knowledge document no longer exists")
                if recycle[2] is None:
                    cur.execute("UPDATE knowledge.recycle_bin SET restored_at=now() WHERE tenant_id=%s AND id=%s", (tenant_id, recycle_bin_id))
                return DocumentRecycleResult(document_id, recycle_bin_id, prior_status)

    def list_recycle_bin(self, *, limit: int = 100, include_restored: bool = False) -> list[dict[str, Any]]:
        """List document recovery records without exposing the document body."""

        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                restored_clause = "" if include_restored else "AND restored_at IS NULL"
                cur.execute(
                    f"""
                    SELECT id,entity_id,snapshot->>'title',snapshot->>'source_uri',
                      snapshot->>'status',snapshot->>'reason',deleted_at,restored_at
                    FROM knowledge.recycle_bin
                    WHERE tenant_id=%s AND entity_kind='semantic.document' {restored_clause}
                    ORDER BY deleted_at DESC,id DESC LIMIT %s
                    """,
                    (tenant_id, limit),
                )
                rows = cur.fetchall()
        return [
            {
                "id": str(row[0]),
                "entity_id": str(row[1]),
                "title": row[2],
                "source_uri": row[3],
                "prior_status": row[4],
                "reason": row[5],
                "deleted_at": row[6],
                "restored_at": row[7],
            }
            for row in rows
        ]
