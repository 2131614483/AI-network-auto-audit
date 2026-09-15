"""M7 run verification: reproducibility compare + read-only rollback verdict.

A verification is an append-only evidence row for a ``success`` isolated run.
The comparator reads each node's ``output_checksum`` ledger row under the run,
matches it against the same slot of a reference run (explicit id, or the
chain's latest success run), and records ``verified`` (full match) or
``drifted`` (any mismatch / missing reference).  A drifted row carries a
read-only ``rollback_verdict`` (action/affected_slots/baseline) that is
advisory only - the comparator spawns no child process and performs no
rollback; the table grants INSERT/SELECT only and is RLS + FORCE.

Idempotency mirrors the run lifecycle (M6): the service layer replays the
control idempotency record, then the DB row (``UNIQUE(tenant_id,
idempotency_key)``) for crash-consistent replays; this module only ever
inserts.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg2.extras import Json

from .contracts import validate_verification

VERIFY_STATUSES = ("verified", "drifted")
VERDICT_ACTIONS = ("re-verify", "re-run-locked-release", "escalate-human")


class RunVerificationError(PermissionError):
    """Fail-closed preflight denied the verification; nothing was written."""


class VerificationConflictError(ValueError):
    """Reference baseline is unusable (missing/self/other-chain/not success)."""


def load_run_baseline(cur: Any, *, tenant_id: UUID, run_id: UUID) -> dict[str, Any]:
    """Tenant-scoped run truth used to anchor a verification."""
    cur.execute(
        """SELECT chain_id, chain_key, chain_checksum, planner_version, status
        FROM topology.execution_runs WHERE id=%s AND tenant_id=%s""",
        (run_id, tenant_id),
    )
    row = cur.fetchone()
    if row is None:
        raise VerificationConflictError(f"run not found: {run_id}")
    return {
        "chain_id": UUID(str(row[0])),
        "chain_key": str(row[1]),
        "chain_checksum": str(row[2]),
        "planner_version": str(row[3]),
        "status": str(row[4]),
    }


def choose_reference(
    cur: Any, *, tenant_id: UUID, run_id: UUID, chain_id: UUID, limit: int = 1
) -> UUID | None:
    """Latest success run of the same chain, never the run being verified."""
    cur.execute(
        """SELECT id FROM topology.execution_runs
        WHERE tenant_id=%s AND chain_id=%s AND status='success' AND id<>%s
        ORDER BY created_at DESC, id LIMIT %s""",
        (tenant_id, chain_id, run_id, limit),
    )
    row = cur.fetchone()
    return UUID(str(row[0])) if row else None


def node_checksums(cur: Any, *, tenant_id: UUID, run_id: UUID) -> dict[str, str]:
    """slot_key -> output_checksum for every succeeded ledger row of a run."""
    cur.execute(
        """SELECT plan_node_slot_key, output_checksum
        FROM topology.execution_ledger
        WHERE tenant_id=%s AND run_id=%s AND status='succeeded'
        ORDER BY ordinal""",
        (tenant_id, run_id),
    )
    return {str(slot_key): str(checksum) for slot_key, checksum in cur.fetchall()}


def build_verdict(
    action: str, affected_slots: list[str], baseline: dict[str, Any]
) -> dict[str, Any]:
    """Read-only advisory projection; never carries an execute action."""
    if action not in VERDICT_ACTIONS:
        raise ValueError(f"invalid rollback verdict action: {action}")
    return {
        "action": action,
        "affected_slots": list(dict.fromkeys(affected_slots)),
        "baseline": {
            "reference_run_id": (
                str(baseline["reference_run_id"]) if baseline.get("reference_run_id") else None
            ),
            "chain_checksum": str(baseline["chain_checksum"]),
            "planner_version": str(baseline["planner_version"]),
        },
    }


def verify_run(
    cur: Any,
    *,
    tenant_id: UUID,
    run_id: UUID,
    reference_run_id: UUID | None,
    reason: str,
    idempotency_key: str,
    trace_id: str,
) -> dict[str, Any]:
    """Compare one success run against a reference and append the verdict row."""
    run = load_run_baseline(cur, tenant_id=tenant_id, run_id=run_id)
    if run["status"] != "success":
        raise RunVerificationError(f"only success runs can be verified; run is {run['status']}")

    reference = reference_run_id
    baseline: dict[str, Any] | None = None
    if reference is None:
        reference = choose_reference(
            cur, tenant_id=tenant_id, run_id=run_id, chain_id=run["chain_id"]
        )
    if reference is not None:
        ref = load_run_baseline(cur, tenant_id=tenant_id, run_id=reference)
        if ref["status"] != "success":
            raise VerificationConflictError("reference run is not success")
        if ref["chain_id"] != run["chain_id"]:
            raise VerificationConflictError("reference run belongs to a different chain")
        baseline = {
            "reference_run_id": reference,
            "chain_checksum": ref["chain_checksum"],
            "planner_version": ref["planner_version"],
        }

    own = node_checksums(cur, tenant_id=tenant_id, run_id=run_id)
    reference_checksums = (
        node_checksums(cur, tenant_id=tenant_id, run_id=reference) if reference is not None else {}
    )
    slots = sorted(own)
    node_total = len(slots)
    if node_total == 0:
        raise RunVerificationError("run has no ledger rows; cannot verify")

    matched = 0
    mismatched = 0
    ref_missing = 0
    affected: list[str] = []
    for slot in slots:
        reference_checksum = reference_checksums.get(slot)
        if reference_checksum is None:
            ref_missing += 1
            affected.append(slot)
        elif reference_checksum == str(own[slot]):
            matched += 1
        else:
            mismatched += 1
            affected.append(slot)

    if matched == node_total:
        status = "verified"
        verdict: dict[str, Any] | None = None
    else:
        status = "drifted"
        if mismatched > 0 and ref_missing == 0:
            action = "re-run-locked-release"
        elif ref_missing > 0 and mismatched == 0:
            action = "re-verify"
        else:
            action = "escalate-human"
        baseline_for_verdict = baseline or {
            "reference_run_id": None,
            "chain_checksum": run["chain_checksum"],
            "planner_version": run["planner_version"],
        }
        verdict = build_verdict(action, affected, baseline_for_verdict)

    cur.execute(
        """INSERT INTO topology.run_verifications
        (tenant_id, run_id, chain_id, chain_key, reference_run_id, status, node_total,
         node_matched, node_mismatched, node_ref_missing, reason, rollback_verdict,
         idempotency_key, trace_id)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            tenant_id,
            run_id,
            run["chain_id"],
            run["chain_key"],
            reference,
            status,
            node_total,
            matched,
            mismatched,
            ref_missing,
            reason,
            Json(verdict) if verdict is not None else None,
            idempotency_key,
            trace_id,
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("run verification insert returned no id")
    return verification_projection(cur, UUID(str(row[0])), tenant_id)


def verification_projection(cur: Any, verification_id: UUID, tenant_id: UUID) -> dict[str, Any]:
    """Strict schema-valid projection of one verification row."""
    cur.execute(
        """SELECT run_id, chain_key, reference_run_id, status, node_total, node_matched,
                  node_mismatched, node_ref_missing, reason, rollback_verdict, trace_id, created_at
        FROM topology.run_verifications WHERE id=%s AND tenant_id=%s""",
        (verification_id, tenant_id),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"verification not found: {verification_id}")
    (
        run_id,
        chain_key,
        reference_run_id,
        status,
        node_total,
        node_matched,
        node_mismatched,
        node_ref_missing,
        reason,
        rollback_verdict,
        trace_id,
        created_at,
    ) = row
    projection: dict[str, Any] = {
        "verification_id": str(verification_id),
        "run_id": str(run_id),
        "chain_key": str(chain_key),
        "reference_run_id": str(reference_run_id) if reference_run_id else None,
        "status": str(status),
        "node_total": int(node_total),
        "node_matched": int(node_matched),
        "node_mismatched": int(node_mismatched),
        "node_ref_missing": int(node_ref_missing),
        "reason": str(reason),
        "rollback_verdict": rollback_verdict if isinstance(rollback_verdict, dict) else None,
        "trace_id": str(trace_id),
        "created_at": created_at.isoformat() if created_at else None,
    }
    validate_verification(projection)
    return projection


def verification_by_key(
    cur: Any, tenant_id: UUID, idempotency_key: str
) -> dict[str, Any] | None:
    """DB-level replay: return the existing row for a repeated key, else None."""
    cur.execute(
        "SELECT id FROM topology.run_verifications WHERE tenant_id=%s AND idempotency_key=%s",
        (tenant_id, idempotency_key),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return verification_projection(cur, UUID(str(row[0])), tenant_id)


def list_verification_rows(cur: Any, tenant_id: UUID, run_id: UUID, limit: int = 50) -> list[dict[str, Any]]:
    """Every verification recorded for one run, newest first."""
    cur.execute(
        """SELECT id FROM topology.run_verifications
        WHERE tenant_id=%s AND run_id=%s
        ORDER BY created_at DESC, id LIMIT %s""",
        (tenant_id, run_id, limit),
    )
    return [
        verification_projection(cur, UUID(str(row[0])), tenant_id) for row in cur.fetchall()
    ]