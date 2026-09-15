"""Durable, tenant-scoped business idempotency for knowledge uploads.

Policy decisions use ``control.idempotency_records``.  That prevents a policy
decision from being duplicated, but is deliberately not a substitute for an
endpoint's business result: an upload retry must not create a second staging
directory or a second semantic document.  This store records the completed
upload envelope separately from the policy decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg2
from psycopg2.extras import Json


class UploadIdempotencyConflict(ValueError):
    """The supplied key is already bound to a different upload body."""


class UploadInProgressError(RuntimeError):
    """A matching request is currently processing and cannot be duplicated."""


@dataclass(frozen=True, slots=True)
class UploadReservation:
    """A completed result or ownership of one in-flight business operation."""

    cached_response: dict[str, Any] | None

    @property
    def is_replay(self) -> bool:
        return self.cached_response is not None


class KnowledgeUploadIdempotencyStore:
    """Reserve, complete and safely replay one tenant's knowledge upload."""

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

    def reserve(self, *, idempotency_key: str, request_hash: str) -> UploadReservation:
        """Claim a new request or replay its one completed business response.

        A second concurrent request with the same body fails closed instead of
        running two parsers.  A previous failed attempt may be retried with the
        exact same key and body; its durable failure note is retained only until
        the next attempt starts.
        """

        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """
                    SELECT request_hash,status,response_json
                    FROM knowledge.upload_idempotency
                    WHERE tenant_id=%s AND idempotency_key=%s
                    FOR UPDATE
                    """,
                    (tenant_id, idempotency_key),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        """
                        INSERT INTO knowledge.upload_idempotency
                          (tenant_id,idempotency_key,request_hash,status)
                        VALUES(%s,%s,%s,'processing')
                        """,
                        (tenant_id, idempotency_key, request_hash),
                    )
                    return UploadReservation(cached_response=None)
                if row[0] != request_hash:
                    raise UploadIdempotencyConflict("Idempotency-Key reused with different request")
                if row[1] == "completed":
                    if not isinstance(row[2], dict):
                        raise RuntimeError("completed knowledge upload has no replayable response")
                    return UploadReservation(cached_response=row[2])
                if row[1] == "processing":
                    raise UploadInProgressError("knowledge upload with this Idempotency-Key is already in progress")
                cur.execute(
                    """
                    UPDATE knowledge.upload_idempotency
                    SET status='processing',failure_detail=NULL,updated_at=now()
                    WHERE tenant_id=%s AND idempotency_key=%s
                    """,
                    (tenant_id, idempotency_key),
                )
                return UploadReservation(cached_response=None)

    def complete(self, *, idempotency_key: str, request_hash: str, response: dict[str, Any]) -> None:
        """Store the business envelope once ingestion has committed."""

        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """
                    UPDATE knowledge.upload_idempotency
                    SET status='completed',response_json=%s,failure_detail=NULL,updated_at=now()
                    WHERE tenant_id=%s AND idempotency_key=%s AND request_hash=%s AND status='processing'
                    """,
                    (Json(response), tenant_id, idempotency_key, request_hash),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("knowledge upload idempotency completion was not owned by this request")

    def fail(self, *, idempotency_key: str, request_hash: str, detail: str) -> None:
        """Record a bounded failure note so an exact retry can safely resume."""

        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """
                    UPDATE knowledge.upload_idempotency
                    SET status='failed',failure_detail=%s,updated_at=now()
                    WHERE tenant_id=%s AND idempotency_key=%s AND request_hash=%s AND status='processing'
                    """,
                    (detail[:1000], tenant_id, idempotency_key, request_hash),
                )
