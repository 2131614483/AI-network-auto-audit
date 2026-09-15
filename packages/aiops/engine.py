from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import psycopg2
from psycopg2.extras import Json, register_uuid

from packages.policy.engine import PolicyEngine

register_uuid()  # type: ignore[no-untyped-call]


class AIOpsEngine:
    def __init__(self, database_url: str, policy: PolicyEngine | None = None, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.policy = policy or PolicyEngine()
        self.tenant_slug = tenant_slug

    def _tenant(self, cur: Any) -> UUID:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {self.tenant_slug}")
        tenant_id: UUID = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return tenant_id

    def ingest_alert(self, source: str, fingerprint: str, severity: str, payload: dict[str, Any]) -> UUID:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """INSERT INTO aiops.alerts(tenant_id,source,fingerprint,severity,payload) VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT(tenant_id,fingerprint) DO UPDATE SET payload=EXCLUDED.payload, severity=EXCLUDED.severity RETURNING id""",
                    (tenant_id, source, fingerprint, severity, Json(payload)),
                )
                alert_row = cur.fetchone()
                if alert_row is None:
                    raise RuntimeError("alert upsert returned no id")
                alert_id = alert_row[0]
                title = f"{source}: {fingerprint}"
                timeline_entry = Json([{"alert_id": str(alert_id), "severity": severity}])
                cur.execute(
                    """SELECT id FROM aiops.incidents
                    WHERE tenant_id=%s AND title=%s AND status IN ('open', 'investigating')
                    ORDER BY created_at DESC LIMIT 1""",
                    (tenant_id, title),
                )
                incident_row = cur.fetchone()
                if incident_row is not None:
                    cur.execute(
                        "UPDATE aiops.incidents SET timeline=timeline || %s::jsonb WHERE id=%s",
                        (timeline_entry, incident_row[0]),
                    )
                else:
                    cur.execute(
                        "INSERT INTO aiops.incidents(tenant_id,title,status,timeline) VALUES(%s,%s,'open',%s) RETURNING id",
                        (tenant_id, title, timeline_entry),
                    )
                    incident_row = cur.fetchone()
                if incident_row is None:
                    raise RuntimeError("incident insert returned no id")
                return UUID(str(incident_row[0]))

    def propose(self, incident_id: UUID, playbook_key: str, command_spec: dict[str, Any], risk_class: str = "high") -> UUID:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """INSERT INTO aiops.remediation_proposals(tenant_id,incident_id,playbook_key,command_spec,risk_class)
                    VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                    (tenant_id, incident_id, playbook_key, Json(command_spec), risk_class),
                )
                proposal_row = cur.fetchone()
                if proposal_row is None:
                    raise RuntimeError("proposal insert returned no id")
                return UUID(str(proposal_row[0]))

    @staticmethod
    def command_hash(command_spec: dict[str, Any]) -> str:
        """Hash a canonical command proposal, never a caller-controlled string."""
        serialized = json.dumps(command_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def request_change(self, proposal_id: UUID, expires_at: str | None = None) -> UUID:
        """Create an approval record bound to the exact remediation arguments."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    "SELECT command_spec FROM aiops.remediation_proposals WHERE tenant_id=%s AND id=%s",
                    (tenant_id, proposal_id),
                )
                proposal = cur.fetchone()
                if proposal is None:
                    raise ValueError("remediation proposal not found")
                command_hash = self.command_hash(proposal[0])
                cur.execute(
                    """INSERT INTO aiops.change_requests(tenant_id,proposal_id,command_hash,status,expires_at)
                    VALUES(%s,%s,%s,'pending',%s)
                    ON CONFLICT(tenant_id,proposal_id,command_hash) DO UPDATE SET expires_at=EXCLUDED.expires_at
                    RETURNING id""",
                    (tenant_id, proposal_id, command_hash, expires_at),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("change request insert returned no id")
                return UUID(str(row[0]))

    def approve_change(self, change_request_id: UUID) -> None:
        """Mark a previously reviewed change request as approved in this MVP."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """UPDATE aiops.change_requests SET status='approved',approved_at=now()
                    WHERE tenant_id=%s AND id=%s AND status='pending'""",
                    (tenant_id, change_request_id),
                )
                if cur.rowcount != 1:
                    raise ValueError("pending change request not found")

    def execute(
        self,
        proposal_id: UUID,
        mode: str = "dry_run",
        change_request_id: UUID | None = None,
        command_spec: dict[str, Any] | None = None,
    ) -> tuple[UUID, str]:
        """Record a simulated execution only after deterministic policy checks.

        Canary mode requires an approved, unexpired change request whose
        stored hash still matches the proposal.  Dry-run does not invoke an
        external actuator, so it remains available for preflight validation.
        """
        if mode not in {"dry_run", "canary"}:
            raise ValueError("mode must be dry_run or canary; live execution is not implemented")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    "SELECT playbook_key,risk_class,command_spec FROM aiops.remediation_proposals WHERE tenant_id=%s AND id=%s",
                    (tenant_id, proposal_id),
                )
                proposal = cur.fetchone()
                if proposal is None:
                    raise ValueError("remediation proposal not found")
                stored_hash = self.command_hash(proposal[2])
                if command_spec is not None and self.command_hash(command_spec) != stored_hash:
                    raise ValueError("command arguments changed after proposal; create a new change request")
                if mode == "canary":
                    if change_request_id is None:
                        raise PermissionError("canary/live execution requires an approved change request")
                    cur.execute(
                        """SELECT command_hash FROM aiops.change_requests
                        WHERE tenant_id=%s AND id=%s AND proposal_id=%s AND status='approved'
                          AND (expires_at IS NULL OR expires_at > now())""",
                        (tenant_id, change_request_id, proposal_id),
                    )
                    approval = cur.fetchone()
                    if approval is None:
                        raise PermissionError("approved, unexpired change request not found")
                    if approval[0] != stored_hash:
                        raise PermissionError("approved change request does not match remediation arguments")
                capability = f"aiops.playbook.{proposal[0]}"
                decision = self.policy.evaluate(capability, risk_class=str(proposal[1]))
                status = "completed" if decision.decision == "allow" else "blocked"
                cur.execute(
                    """INSERT INTO aiops.executions(tenant_id,proposal_id,change_request_id,command_hash,mode,status,output,started_at,finished_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,now(),now()) RETURNING id""",
                    (
                        tenant_id,
                        proposal_id,
                        change_request_id,
                        stored_hash,
                        mode,
                        status,
                        Json({"decision": decision.decision, "reason": decision.reason, "simulated": True}),
                    ),
                )
                execution_row = cur.fetchone()
                if execution_row is None:
                    raise RuntimeError("execution insert returned no id")
                return UUID(str(execution_row[0])), status

    def verify(self, execution_id: UUID, healthy: bool) -> str:
        """Independently verify a simulated canary and trip a local circuit breaker."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    "SELECT proposal_id,mode,status FROM aiops.executions WHERE tenant_id=%s AND id=%s",
                    (tenant_id, execution_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError("execution not found")
                if row[1] != "canary" or row[2] != "completed":
                    raise ValueError("only completed canary executions can be verified")
                if healthy:
                    cur.execute("UPDATE aiops.executions SET status='verified' WHERE id=%s", (execution_id,))
                    return "verified"
                cur.execute("UPDATE aiops.executions SET status='rolled_back' WHERE id=%s", (execution_id,))
                cur.execute("UPDATE aiops.remediation_proposals SET status='circuit_open' WHERE id=%s", (row[0],))
                return "rolled_back"
