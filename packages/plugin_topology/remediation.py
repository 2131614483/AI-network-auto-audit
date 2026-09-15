"""M8 remediation: governed disposition of drifted verifications.

A remediation proposal is the evidence-recorded next step for an M7
``drifted`` verification: it anchors the recommended action (from the closed
rollback-verdict enum), the affected slots and the baseline summary, stays
``pending_approval`` forever as a row, and reaches a *terminal* state through
the append-only decision ledger (approve/reject/close).  An approved
``re-run-locked-release`` proposal may dispatch a governed re-run through the
existing M6 run machinery - the link is recorded in ``remediation_run_links``
- while ``re-verify``/``escalate-human`` never trigger execution.  No child
process is ever spawned by this module; all three surfaces grant INSERT/SELECT
only and are RLS + FORCE.  Terminal status is *derived* from the immutable
ledgers, never stored by UPDATE.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg2.extras import Json

from .contracts import validate_proposal

REMEDIATION_ACTIONS = ("re-verify", "re-run-locked-release", "escalate-human")
DECISION_VALUES = ("approve", "reject", "close")

_STATUS_BY_DECISION = {"approve": "approved", "reject": "rejected", "close": "closed"}


class RemediationError(PermissionError):
    """Fail-closed preflight denied the remediation; nothing was written."""


class RemediationConflictError(ValueError):
    """Lifecycle conflict (not drifted / already decided / not approved for re-run)."""


def load_verification_anchor(
    cur: Any, *, tenant_id: UUID, verification_id: UUID
) -> dict[str, Any]:
    """Tenant-scoped drift truth a proposal anchors on."""
    cur.execute(
        """SELECT run_id, chain_id, chain_key, status, rollback_verdict
        FROM topology.run_verifications WHERE id=%s AND tenant_id=%s""",
        (verification_id, tenant_id),
    )
    row = cur.fetchone()
    if row is None:
        raise RemediationConflictError(f"verification not found: {verification_id}")
    verdict = row[4] if isinstance(row[4], dict) else None
    return {
        "run_id": UUID(str(row[0])),
        "chain_id": UUID(str(row[1])),
        "chain_key": str(row[2]),
        "status": str(row[3]),
        "rollback_verdict": verdict,
    }


def create_proposal(
    cur: Any,
    *,
    tenant_id: UUID,
    verification_id: UUID,
    action: str,
    reason: str,
    idempotency_key: str,
    trace_id: str,
) -> dict[str, Any]:
    """Open a remediation proposal for one drifted verification (append-only)."""
    if action not in REMEDIATION_ACTIONS:
        raise RemediationError(f"invalid remediation action: {action}")
    anchor = load_verification_anchor(cur, tenant_id=tenant_id, verification_id=verification_id)
    if anchor["status"] != "drifted":
        raise RemediationError(
            f"only drifted verifications can be remediated; got {anchor['status']}"
        )
    verdict = anchor["rollback_verdict"]
    if verdict is None or not verdict.get("affected_slots"):
        raise RemediationError("drifted verification carries no rollback verdict; cannot remediate")
    cur.execute(
        """INSERT INTO topology.remediation_proposals
        (tenant_id, verification_id, run_id, chain_id, chain_key, status, action,
         affected_slots, baseline, reason, idempotency_key, trace_id)
        VALUES(%s,%s,%s,%s,%s,'pending_approval',%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            tenant_id,
            verification_id,
            anchor["run_id"],
            anchor["chain_id"],
            anchor["chain_key"],
            action,
            Json(list(verdict["affected_slots"])),
            Json(verdict["baseline"]),
            reason,
            idempotency_key,
            trace_id,
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("remediation proposal insert returned no id")
    return proposal_projection(cur, UUID(str(row[0])), tenant_id)


def decide_proposal(
    cur: Any,
    *,
    tenant_id: UUID,
    proposal_id: UUID,
    decision: str,
    approver: str,
    reason: str,
    idempotency_key: str,
    trace_id: str,
) -> dict[str, Any]:
    """Record one terminal decision for a proposal (append-only, once)."""
    if decision not in DECISION_VALUES:
        raise RemediationConflictError(f"invalid decision: {decision}")
    proposal = proposal_projection(cur, proposal_id, tenant_id)
    if proposal["status"] != "pending_approval":
        raise RemediationConflictError(
            f"proposal already finalized as {proposal['status']}; decisions are irreversible"
        )
    if decision == "close" and proposal["action"] != "escalate-human":
        raise RemediationConflictError("close decision is only valid for escalate-human proposals")
    cur.execute(
        """INSERT INTO topology.remediation_decisions
        (tenant_id, proposal_id, decision, approver, reason, idempotency_key, trace_id)
        VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (tenant_id, proposal_id, decision, approver, reason, idempotency_key, trace_id),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("remediation decision insert returned no id")
    return proposal_projection(cur, proposal_id, tenant_id)


def link_remediation_run(
    cur: Any,
    *,
    tenant_id: UUID,
    proposal_id: UUID,
    run_id: UUID,
    idempotency_key: str,
    trace_id: str,
) -> None:
    """Record the proposal -> run lineage of a governed re-run (append-only)."""
    cur.execute(
        """INSERT INTO topology.remediation_run_links
        (tenant_id, proposal_id, run_id, idempotency_key, trace_id)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT (tenant_id, idempotency_key) DO NOTHING""",
        (tenant_id, proposal_id, run_id, idempotency_key, trace_id),
    )


def proposal_projection(cur: Any, proposal_id: UUID, tenant_id: UUID) -> dict[str, Any]:
    """Strict schema-valid projection: status and re-run id derived from ledgers."""
    cur.execute(
        """SELECT verification_id, run_id, chain_key, action, affected_slots, baseline,
                  reason, trace_id, created_at
        FROM topology.remediation_proposals WHERE id=%s AND tenant_id=%s""",
        (proposal_id, tenant_id),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"remediation proposal not found: {proposal_id}")
    (
        verification_id,
        run_id,
        chain_key,
        action,
        affected_slots,
        baseline,
        reason,
        trace_id,
        created_at,
    ) = row

    cur.execute(
        """SELECT decision FROM topology.remediation_decisions
        WHERE tenant_id=%s AND proposal_id=%s ORDER BY created_at DESC, id LIMIT 1""",
        (tenant_id, proposal_id),
    )
    decision_row = cur.fetchone()
    status = (
        _STATUS_BY_DECISION[str(decision_row[0])] if decision_row is not None else "pending_approval"
    )

    remediation_run_id: str | None = None
    if status == "approved":
        cur.execute(
            """SELECT run_id FROM topology.remediation_run_links
            WHERE tenant_id=%s AND proposal_id=%s ORDER BY created_at DESC, id LIMIT 1""",
            (tenant_id, proposal_id),
        )
        link_row = cur.fetchone()
        if link_row is not None:
            remediation_run_id = str(link_row[0])

    projection: dict[str, Any] = {
        "proposal_id": str(proposal_id),
        "verification_id": str(verification_id),
        "run_id": str(run_id),
        "chain_key": str(chain_key),
        "status": status,
        "action": str(action),
        "affected_slots": list(affected_slots or []),
        "baseline": baseline if isinstance(baseline, dict) else {},
        "remediation_run_id": remediation_run_id,
        "reason": str(reason),
        "trace_id": str(trace_id),
        "created_at": created_at.isoformat() if created_at else None,
    }
    validate_proposal(projection)
    return projection


def proposal_by_key(
    cur: Any, tenant_id: UUID, idempotency_key: str
) -> dict[str, Any] | None:
    """DB-level replay: return the existing proposal for a repeated key, else None."""
    cur.execute(
        "SELECT id FROM topology.remediation_proposals WHERE tenant_id=%s AND idempotency_key=%s",
        (tenant_id, idempotency_key),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return proposal_projection(cur, UUID(str(row[0])), tenant_id)


def list_proposal_rows(
    cur: Any,
    tenant_id: UUID,
    *,
    run_id: UUID | None = None,
    verification_id: UUID | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Proposals filtered by run/verification (newest first); limit applies."""
    conditions = ["tenant_id=%s"]
    params: list[Any] = [tenant_id]
    if run_id is not None:
        conditions.append("run_id=%s")
        params.append(run_id)
    if verification_id is not None:
        conditions.append("verification_id=%s")
        params.append(verification_id)
    params.append(limit)
    cur.execute(
        f"""SELECT id FROM topology.remediation_proposals
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC, id LIMIT %s""",
        tuple(params),
    )
    return [proposal_projection(cur, UUID(str(row[0])), tenant_id) for row in cur.fetchall()]