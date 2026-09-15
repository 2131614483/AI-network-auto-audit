"""Phase 9 AIOps governance queries for local simulations only.

This service deliberately has no actuator, command runner, network client or
``live`` execution method.  It exposes tenant-scoped lineage for records that
were already created by the local simulation engine and retains one immutable
human verification per completed Canary record.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg2
from psycopg2.extras import Json, register_uuid

register_uuid()  # type: ignore[no-untyped-call]


class AIOpsGovernanceService:
    """Read and verify only the AIOps local simulation evidence chain."""

    def __init__(self, database_url: str, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.tenant_slug = tenant_slug

    def _tenant(self, cur: Any) -> Any:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {self.tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        return tenant_id

    def list_incidents(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return bounded AIOps incident summaries within the active tenant."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT i.id,i.title,i.status,i.created_at,
                    COALESCE(jsonb_array_length(i.timeline), 0),
                    count(DISTINCT p.id),count(DISTINCT c.id),count(DISTINCT e.id)
                    FROM aiops.incidents i
                    LEFT JOIN aiops.remediation_proposals p
                      ON p.incident_id=i.id AND p.tenant_id=i.tenant_id
                    LEFT JOIN aiops.change_requests c
                      ON c.proposal_id=p.id AND c.tenant_id=i.tenant_id
                    LEFT JOIN aiops.executions e
                      ON e.proposal_id=p.id AND e.tenant_id=i.tenant_id
                    WHERE i.tenant_id=%s
                    GROUP BY i.id,i.title,i.status,i.created_at,i.timeline
                    ORDER BY i.created_at DESC LIMIT %s""",
                    (tenant_id, limit),
                )
                return [
                    {
                        "id": str(row[0]), "title": row[1], "status": row[2], "created_at": row[3],
                        "alert_count": int(row[4]), "proposal_count": int(row[5]),
                        "change_request_count": int(row[6]), "execution_count": int(row[7]),
                        "simulated_only": True,
                    }
                    for row in cur.fetchall()
                ]

    def get_incident_lineage(self, incident_id: str) -> dict[str, Any]:
        """Return a local-simulation-only lineage without exposing command specs."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT id,title,status,root_cause,timeline,created_at
                    FROM aiops.incidents WHERE tenant_id=%s AND id=%s""",
                    (tenant_id, incident_id),
                )
                incident = cur.fetchone()
                if incident is None:
                    raise ValueError("AIOps incident not found")

                cur.execute(
                    """SELECT DISTINCT a.id,a.source,a.fingerprint,a.severity,a.occurred_at
                    FROM aiops.incidents i
                    CROSS JOIN LATERAL jsonb_array_elements(i.timeline) AS event(entry)
                    JOIN aiops.alerts a ON a.id::text=event.entry->>'alert_id' AND a.tenant_id=i.tenant_id
                    WHERE i.tenant_id=%s AND i.id=%s ORDER BY a.occurred_at ASC""",
                    (tenant_id, incident_id),
                )
                alerts = [
                    {"id": str(row[0]), "source": row[1], "fingerprint": row[2], "severity": row[3], "occurred_at": row[4]}
                    for row in cur.fetchall()
                ]

                cur.execute(
                    """SELECT id,playbook_key,risk_class,status,created_at
                    FROM aiops.remediation_proposals
                    WHERE tenant_id=%s AND incident_id=%s ORDER BY created_at ASC""",
                    (tenant_id, incident_id),
                )
                proposals = [
                    {"id": str(row[0]), "playbook_key": row[1], "risk_class": row[2], "status": row[3], "created_at": row[4]}
                    for row in cur.fetchall()
                ]

                cur.execute(
                    """SELECT c.id,c.proposal_id,c.command_hash,c.status,c.approved_at,c.expires_at,c.created_at
                    FROM aiops.change_requests c
                    JOIN aiops.remediation_proposals p ON p.id=c.proposal_id AND p.tenant_id=c.tenant_id
                    WHERE c.tenant_id=%s AND p.incident_id=%s ORDER BY c.created_at ASC""",
                    (tenant_id, incident_id),
                )
                change_requests = [
                    {
                        "id": str(row[0]), "proposal_id": str(row[1]), "command_hash": row[2], "status": row[3],
                        "approved_at": row[4], "expires_at": row[5], "created_at": row[6],
                    }
                    for row in cur.fetchall()
                ]

                cur.execute(
                    """SELECT e.id,e.proposal_id,e.change_request_id,e.command_hash,e.mode,e.status,e.output,
                    e.started_at,e.finished_at
                    FROM aiops.executions e
                    JOIN aiops.remediation_proposals p ON p.id=e.proposal_id AND p.tenant_id=e.tenant_id
                    WHERE e.tenant_id=%s AND p.incident_id=%s ORDER BY e.started_at ASC NULLS LAST,e.id ASC""",
                    (tenant_id, incident_id),
                )
                executions = []
                for row in cur.fetchall():
                    output = row[6] if isinstance(row[6], dict) else {}
                    executions.append(
                        {
                            "id": str(row[0]), "proposal_id": str(row[1]),
                            "change_request_id": str(row[2]) if row[2] is not None else None,
                            "command_hash": row[3], "mode": row[4], "status": row[5],
                            "simulated": bool(output.get("simulated", True)),
                            "started_at": row[7], "finished_at": row[8],
                        }
                    )

                cur.execute(
                    """SELECT v.id,v.execution_id,v.outcome,v.reviewer_label,v.trace_id,v.created_at
                    FROM aiops.execution_verifications v
                    JOIN aiops.executions e ON e.id=v.execution_id AND e.tenant_id=v.tenant_id
                    JOIN aiops.remediation_proposals p ON p.id=e.proposal_id AND p.tenant_id=e.tenant_id
                    WHERE v.tenant_id=%s AND p.incident_id=%s ORDER BY v.created_at ASC""",
                    (tenant_id, incident_id),
                )
                verifications = [
                    {
                        "id": str(row[0]), "execution_id": str(row[1]), "outcome": row[2],
                        "reviewer_label": row[3], "trace_id": str(row[4]), "created_at": row[5],
                    }
                    for row in cur.fetchall()
                ]
                return {
                    "kind": "aiops_governance_chain", "incident_id": str(incident[0]), "simulated_only": True,
                    "incident": {
                        "id": str(incident[0]), "title": incident[1], "status": incident[2],
                        "root_cause": incident[3] or {}, "timeline": incident[4] or [], "created_at": incident[5],
                    },
                    "alerts": alerts, "proposals": proposals, "change_requests": change_requests,
                    "executions": executions, "verifications": verifications,
                }

    def verify_canary(
        self,
        execution_id: str,
        outcome: str,
        reviewer_label: str,
        trace_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Record one reviewed outcome for a completed local Canary simulation.

        A matching second call returns the original verification.  A conflicting
        result is rejected so a completed review cannot be silently rewritten.
        """
        normalized_reviewer = reviewer_label.strip()
        if outcome not in {"healthy", "rollback"}:
            raise ValueError("outcome must be healthy or rollback")
        if not 2 <= len(normalized_reviewer) <= 120:
            raise ValueError("reviewer_label must contain between 2 and 120 characters")
        if not 1 <= len(idempotency_key) <= 255:
            raise ValueError("idempotency key must contain between 1 and 255 characters")
        try:
            normalized_trace_id = UUID(trace_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("trace_id must be a UUID") from exc

        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT e.proposal_id,e.mode,e.status
                    FROM aiops.executions e WHERE e.tenant_id=%s AND e.id=%s FOR UPDATE""",
                    (tenant_id, execution_id),
                )
                execution = cur.fetchone()
                if execution is None:
                    raise ValueError("AIOps execution not found")
                cur.execute(
                    """SELECT id,outcome FROM aiops.execution_verifications
                    WHERE tenant_id=%s AND execution_id=%s""",
                    (tenant_id, execution_id),
                )
                existing = cur.fetchone()
                if existing is not None:
                    if existing[1] != outcome:
                        raise ValueError("execution already has a final verification with a different outcome")
                    return {
                        "execution_id": execution_id, "verification_id": str(existing[0]),
                        "status": "verified" if outcome == "healthy" else "rolled_back", "idempotent": True,
                    }
                if execution[1] != "canary" or execution[2] != "completed":
                    raise ValueError("only completed canary simulations can be verified")
                cur.execute(
                    """INSERT INTO aiops.execution_verifications(
                    tenant_id,execution_id,outcome,reviewer_label,trace_id,idempotency_key)
                    VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (tenant_id, execution_id, outcome, normalized_reviewer, normalized_trace_id, idempotency_key),
                )
                verification = cur.fetchone()
                if verification is None:
                    raise RuntimeError("AIOps verification insert returned no id")
                status = "verified" if outcome == "healthy" else "rolled_back"
                cur.execute(
                    """UPDATE aiops.executions SET status=%s,
                    output=COALESCE(output,'{}'::jsonb) || %s::jsonb
                    WHERE tenant_id=%s AND id=%s""",
                    (
                        status,
                        Json({"simulated": True, "verification_id": str(verification[0]), "verification_outcome": outcome}),
                        tenant_id,
                        execution_id,
                    ),
                )
                if outcome == "rollback":
                    cur.execute(
                        """UPDATE aiops.remediation_proposals SET status='circuit_open'
                        WHERE tenant_id=%s AND id=%s""",
                        (tenant_id, execution[0]),
                    )
                return {
                    "execution_id": execution_id, "verification_id": str(verification[0]),
                    "status": status, "idempotent": False,
                }
