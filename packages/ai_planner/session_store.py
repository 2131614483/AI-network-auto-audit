"""Durable plan-only sessions and immutable events behind recorded decisions.

The service must obtain a Policy Gateway decision before calling a mutation.
This store verifies that decision's tenant, trace, capability and ALLOW result;
it never creates policy rules or upgrades the schema.  Leases fence both stale
and expired writers, while event/checkpoint commits form the recovery boundary.
"""

from __future__ import annotations

import builtins
import re
from typing import Any
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from .evidence import PlanningContext, PlanningRequest, fingerprint

WRITE_CAPABILITY = "topology.collaborative.plan"
TERMINAL_STATUSES = frozenset({
    "accepted", "blocked_input", "blocked_capability", "blocked_policy",
    "budget_exhausted", "stalled", "failed", "cancelled",
})


class PlanningSessionConflict(ValueError):
    """An idempotency key or terminal result was reused with different data."""


class PlanningSessionFenced(RuntimeError):
    """A writer's lease expired, was replaced or no longer owns a running session."""


class PlanningSessionNotFound(LookupError):
    """The session is not visible in the supplied tenant."""


class PlanningSessionPolicyDenied(PermissionError):
    """A durable decision does not authorize this tenant and trace to write."""


def _lease_seconds(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
        raise ValueError("lease_seconds must be an integer between 1 and 3600")
    return value


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    return value


def _projection(row: Any, *, idempotent: bool = False) -> dict[str, Any]:
    result = dict(row)
    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    if "request_json" in result:
        result["session_id"] = str(result["id"])
    result["model_access"] = (result.get("checkpoint_json") or {}).get("model_access")
    result["idempotent"] = idempotent
    return result


class PlanningSessionStore:
    """Tenant-scoped PostgreSQL projection plus append-only planning evidence."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @staticmethod
    def _tenant(cur: Any, tenant_id: UUID) -> None:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))

    @staticmethod
    def _authorize(cur: Any, context: PlanningContext, decision_id: UUID) -> None:
        cur.execute(
            """SELECT d.id FROM policy.decisions d
               JOIN policy.tool_calls t ON t.id=d.tool_call_id AND t.tenant_id=d.tenant_id
               WHERE d.id=%s AND d.tenant_id=%s AND d.decision='ALLOW'
                 AND t.trace_id=%s AND t.capability=%s""",
            (str(decision_id), str(context.tenant_id), str(context.trace_id), WRITE_CAPABILITY),
        )
        if cur.fetchone() is None:
            raise PlanningSessionPolicyDenied("a recorded ALLOW for this tenant, trace and planning capability is required")

    @staticmethod
    def _session(cur: Any, tenant_id: UUID, session_id: UUID, *, lock: bool = False) -> Any:
        cur.execute(
            "SELECT *, lease_expires_at > clock_timestamp() AS lease_current "
            "FROM topology.planning_sessions WHERE tenant_id=%s AND id=%s"
            + (" FOR UPDATE" if lock else ""),
            (str(tenant_id), str(session_id)),
        )
        row = cur.fetchone()
        if row is None:
            raise PlanningSessionNotFound("planning session not found")
        return row

    @staticmethod
    def _same_trace(row: Any, context: PlanningContext) -> None:
        if str(row["trace_id"]) != str(context.trace_id):
            raise PlanningSessionPolicyDenied("planning context does not match the session trace")

    @classmethod
    def _owned(cls, cur: Any, context: PlanningContext, lease_token: UUID | str) -> Any:
        row = cls._session(cur, context.tenant_id, context.session_id, lock=True)
        cls._same_trace(row, context)
        if (row["status"] != "running" or not row["lease_current"]
                or str(row["lease_token"]) != str(lease_token)):
            raise PlanningSessionFenced("planning session lease is expired or no longer owned")
        return row

    @staticmethod
    def _event(
        cur: Any, row: Any, context: PlanningContext, event_key: str, event_type: str,
        payload: dict[str, Any], decision_id: UUID, checkpoint: dict[str, Any] | None = None,
    ) -> bool:
        if not isinstance(event_key, str) or not 1 <= len(event_key) <= 200:
            raise ValueError("event_key must contain 1..200 characters")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", event_type):
            raise ValueError("invalid event_type")
        digest = fingerprint({"type": event_type, "round": context.round_no,
                              "payload": payload, "checkpoint": checkpoint})
        cur.execute(
            "SELECT payload_hash FROM topology.planning_session_events "
            "WHERE tenant_id=%s AND session_id=%s AND event_key=%s",
            (str(context.tenant_id), str(context.session_id), event_key),
        )
        existing = cur.fetchone()
        if existing is not None:
            if existing["payload_hash"] != digest:
                raise PlanningSessionConflict("planning event key reused with different content")
            return False
        event_seq = int(row["next_event_seq"]) + 1
        cur.execute(
            """INSERT INTO topology.planning_session_events
               (tenant_id,session_id,trace_id,event_seq,event_key,event_type,round_no,payload,payload_hash,decision_id)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (str(context.tenant_id), str(context.session_id), str(context.trace_id), event_seq,
             event_key, event_type, context.round_no, Json(payload), digest, str(decision_id)),
        )
        cur.execute(
            """UPDATE topology.planning_sessions SET next_event_seq=%s,updated_at=now(),decision_id=%s,
               checkpoint_json=COALESCE(%s,checkpoint_json) WHERE tenant_id=%s AND id=%s""",
            (event_seq, str(decision_id), Json(checkpoint) if checkpoint is not None else None,
             str(context.tenant_id), str(context.session_id)),
        )
        return True

    def create(
        self, context: PlanningContext, request: PlanningRequest, decision_id: UUID,
        *, model_access: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = request.model_dump(mode="json")
        digest = fingerprint(body)
        with psycopg2.connect(self.database_url) as connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            self._tenant(cur, context.tenant_id)
            self._authorize(cur, context, decision_id)
            cur.execute(
                """INSERT INTO topology.planning_sessions
                   (id,tenant_id,trace_id,idempotency_key,request_hash,request_json,decision_id)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(tenant_id,idempotency_key) DO NOTHING RETURNING id""",
                (str(context.session_id), str(context.tenant_id), str(context.trace_id),
                 request.idempotency_key, digest, Json(body), str(decision_id)),
            )
            created = cur.fetchone() is not None
            cur.execute(
                "SELECT * FROM topology.planning_sessions WHERE tenant_id=%s AND idempotency_key=%s",
                (str(context.tenant_id), request.idempotency_key),
            )
            row = cur.fetchone()
            assert row is not None
            if row["request_hash"] != digest:
                raise PlanningSessionConflict("Idempotency-Key reused with a different planning request")
            if created:
                payload: dict[str, Any] = {"request_hash": digest}
                checkpoint = None
                if model_access is not None:
                    payload["model_access"] = model_access
                    checkpoint = {"model_access": model_access}
                self._event(cur, row, context, "session-created", "created", payload, decision_id, checkpoint=checkpoint)
                row = self._session(cur, context.tenant_id, context.session_id)
            return _projection(row, idempotent=not created)

    def get(self, tenant_id: UUID, session_id: UUID) -> dict[str, Any]:
        with psycopg2.connect(self.database_url) as connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            self._tenant(cur, tenant_id)
            return _projection(self._session(cur, tenant_id, session_id))

    def list(self, tenant_id: UUID, limit: int = 50) -> builtins.list[dict[str, Any]]:
        count = _limit(limit)
        with psycopg2.connect(self.database_url) as connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            self._tenant(cur, tenant_id)
            cur.execute(
                "SELECT * FROM topology.planning_sessions WHERE tenant_id=%s ORDER BY created_at DESC,id LIMIT %s",
                (str(tenant_id), count),
            )
            return [_projection(row) for row in cur.fetchall()]

    def list_pending(self, tenant_id: UUID, limit: int = 1) -> builtins.list[dict[str, Any]]:
        count = _limit(limit)
        with psycopg2.connect(self.database_url) as connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            self._tenant(cur, tenant_id)
            cur.execute(
                """SELECT * FROM topology.planning_sessions WHERE tenant_id=%s
                   AND (status='queued' OR (status='running' AND lease_expires_at < clock_timestamp()))
                   ORDER BY created_at,id LIMIT %s""",
                (str(tenant_id), count),
            )
            return [_projection(row) for row in cur.fetchall()]

    def claim(
        self, context: PlanningContext, worker_id: str, decision_id: UUID, lease_seconds: int = 300,
    ) -> dict[str, Any] | None:
        seconds = _lease_seconds(lease_seconds)
        if not worker_id.strip() or len(worker_id) > 200:
            raise ValueError("worker_id must contain 1..200 characters")
        token = uuid4()
        with psycopg2.connect(self.database_url) as connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            self._tenant(cur, context.tenant_id)
            self._authorize(cur, context, decision_id)
            row = self._session(cur, context.tenant_id, context.session_id, lock=True)
            self._same_trace(row, context)
            if row["status"] in TERMINAL_STATUSES or (row["status"] == "running" and row["lease_current"]):
                return None
            cur.execute(
                """UPDATE topology.planning_sessions SET status='running',worker_id=%s,lease_token=%s,
                   lease_expires_at=clock_timestamp()+make_interval(secs => %s),claim_count=claim_count+1,
                   started_at=COALESCE(started_at,now()),updated_at=now(),decision_id=%s
                   WHERE tenant_id=%s AND id=%s""",
                (worker_id, str(token), seconds, str(decision_id), str(context.tenant_id), str(context.session_id)),
            )
            self._event(cur, row, context, f"claim-{token}", "claimed", {"worker_id": worker_id}, decision_id)
            return _projection(self._session(cur, context.tenant_id, context.session_id))

    def append(
        self, context: PlanningContext, event_key: str, event_type: str, payload: dict[str, Any],
        decision_id: UUID, lease_token: UUID | str, checkpoint: dict[str, Any] | None = None,
    ) -> None:
        with psycopg2.connect(self.database_url) as connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            self._tenant(cur, context.tenant_id)
            self._authorize(cur, context, decision_id)
            row = self._owned(cur, context, lease_token)
            self._event(cur, row, context, event_key, event_type, payload, decision_id, checkpoint)

    def renew(
        self, context: PlanningContext, lease_token: UUID | str, decision_id: UUID, lease_seconds: int = 300,
    ) -> dict[str, Any]:
        seconds = _lease_seconds(lease_seconds)
        with psycopg2.connect(self.database_url) as connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            self._tenant(cur, context.tenant_id)
            self._authorize(cur, context, decision_id)
            row = self._owned(cur, context, lease_token)
            cur.execute(
                """UPDATE topology.planning_sessions
                   SET lease_expires_at=clock_timestamp()+make_interval(secs => %s),updated_at=now()
                   WHERE tenant_id=%s AND id=%s""",
                (seconds, str(context.tenant_id), str(context.session_id)),
            )
            self._event(cur, row, context, f"renew-{uuid4()}", "lease_renewed", {"lease_seconds": seconds}, decision_id)
            return _projection(self._session(cur, context.tenant_id, context.session_id))

    def finish(
        self, context: PlanningContext, status: str, result: dict[str, Any], decision_id: UUID,
        lease_token: UUID | str,
    ) -> dict[str, Any]:
        if status not in TERMINAL_STATUSES:
            raise ValueError("a terminal planning status is required")
        fingerprint(result)  # reject non-JSON/non-finite values before opening the write transaction
        with psycopg2.connect(self.database_url) as connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            self._tenant(cur, context.tenant_id)
            self._authorize(cur, context, decision_id)
            row = self._session(cur, context.tenant_id, context.session_id, lock=True)
            self._same_trace(row, context)
            if str(row["lease_token"]) != str(lease_token):
                raise PlanningSessionFenced("planning session lease is no longer owned")
            if row["status"] in TERMINAL_STATUSES:
                if row["status"] != status or row["result_json"] != result:
                    raise PlanningSessionConflict("a completed planning session cannot be replaced")
                return _projection(row, idempotent=True)
            if row["status"] != "running" or not row["lease_current"]:
                raise PlanningSessionFenced("planning session lease is expired or no longer running")
            self._event(cur, row, context, "session-finished", "finished", {"status": status, "result": result}, decision_id)
            cur.execute(
                """UPDATE topology.planning_sessions SET status=%s,result_json=%s,finished_at=now(),
                   updated_at=now(),lease_expires_at=NULL,decision_id=%s WHERE tenant_id=%s AND id=%s""",
                (status, Json(result), str(decision_id), str(context.tenant_id), str(context.session_id)),
            )
            return _projection(self._session(cur, context.tenant_id, context.session_id))

    def events(self, tenant_id: UUID, session_id: UUID) -> builtins.list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            self._tenant(cur, tenant_id)
            self._session(cur, tenant_id, session_id)
            cur.execute(
                "SELECT * FROM topology.planning_session_events WHERE tenant_id=%s AND session_id=%s ORDER BY event_seq",
                (str(tenant_id), str(session_id)),
            )
            return [_projection(row) for row in cur.fetchall()]
