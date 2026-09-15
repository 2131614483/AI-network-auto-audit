"""Plugin Topology M8 unit tests: remediation guard rails (zero-DB).

The remediation module is a pure-DB evidence layer; its guard branches (action
enum closure, decision enum closure, close-only-for-escalate-human) reject
before any statement touches a cursor, so they are unit-testable without a
database.  A fake cursor unit-covers the derived-status projection (the core
append-only semantics): pending_approval when no decision exists, terminal
status derived from the immutable decision ledger, and ``remediation_run_id``
only surfaced on an approved proposal that actually re-ran.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from packages.plugin_topology.remediation import (
    RemediationConflictError,
    RemediationError,
    create_proposal,
    decide_proposal,
    proposal_projection,
)


class _FakeCursor:
    """Scripted cursor: each query consumes rows from a per-query queue."""

    def __init__(self, *result_sets: list[tuple[Any, ...]]) -> None:
        self._queues = list(result_sets)
        self.calls: list[str] = []

    def execute(self, statement: str, _params: tuple[Any, ...] | None = None) -> None:
        self.calls.append(statement)

    def fetchone(self) -> tuple[Any, ...] | None:
        if not self._queues:
            return None
        rows = self._queues.pop(0)
        return rows[0] if rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        if not self._queues:
            return []
        return self._queues.pop(0)


# -- zero-DB guard rails ----------------------------------------------------------


def test_create_proposal_rejects_invalid_action_without_cursor() -> None:
    with pytest.raises(RemediationError, match="invalid remediation action"):
        create_proposal(
            None,  # type: ignore[arg-type]
            tenant_id=uuid4(),
            verification_id=uuid4(),
            action="apply-cr",
            reason="M8 单元：枚举闭合",
            idempotency_key="k1",
            trace_id="t1",
        )


def test_create_proposal_rejects_non_drifted_anchor_without_insert() -> None:
    """A non-drifted anchor fails closed before the INSERT statement."""
    cur = _FakeCursor(
        [
            (
                uuid4(),  # run_id
                uuid4(),  # chain_id
                "chain-test0000000001",  # chain_key
                "verified",  # status -> must fail closed
                None,  # rollback_verdict
            )
        ]
    )
    with pytest.raises(RemediationError, match="only drifted verifications"):
        create_proposal(
            cur,
            tenant_id=uuid4(),
            verification_id=uuid4(),
            action="re-verify",
            reason="M8 单元：非 drifted 拒绝",
            idempotency_key="k1",
            trace_id="t1",
        )
    assert not any("INSERT INTO topology.remediation_proposals" in call for call in cur.calls)


def test_decide_proposal_rejects_invalid_decision_without_cursor() -> None:
    with pytest.raises(RemediationConflictError, match="invalid decision"):
        decide_proposal(
            None,  # type: ignore[arg-type]
            tenant_id=uuid4(),
            proposal_id=uuid4(),
            decision="maybe",
            approver="M8-tester",
            reason="M8 单元：决策枚举闭合",
            idempotency_key="k2",
            trace_id="t2",
        )


# -- derived-status projection (append-only semantics) ----------------------------


def _proposal_row() -> tuple[Any, ...]:
    return (
        uuid4(),  # verification_id
        uuid4(),  # run_id
        "chain-testabcd12345678",  # chain_key (16 hex-safe chars)
        "re-run-locked-release",  # action
        ["audit.ledger.validate"],  # affected_slots
        {"reference_run_id": str(uuid4()), "chain_checksum": "a" * 64, "planner_version": "1.0.0"},
        "M8 单元投影",  # reason
        str(uuid4()),  # trace_id
        datetime(2026, 9, 7, 8, 0, 0, tzinfo=UTC),  # created_at
    )


def _one(row: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    """Wrap a single DB row as one result set."""
    return [row]


def test_projection_pending_without_any_decision() -> None:
    tenant_id = uuid4()
    proposal_id = uuid4()
    cur = _FakeCursor(
        _one(_proposal_row()),  # proposal
        [],  # no decision rows -> pending_approval
    )
    projection = proposal_projection(cur, proposal_id, tenant_id)
    assert projection["status"] == "pending_approval"
    assert projection["remediation_run_id"] is None
    assert projection["action"] == "re-run-locked-release"
    assert projection["chain_key"] == "chain-testabcd12345678"


def test_projection_approved_from_approve_decision() -> None:
    tenant_id = uuid4()
    proposal_id = uuid4()
    cur = _FakeCursor(
        _one(_proposal_row()),  # proposal
        [("approve",)],  # latest decision
        [],  # no re-run link yet -> remediation_run_id None
    )
    projection = proposal_projection(cur, proposal_id, tenant_id)
    assert projection["status"] == "approved"
    assert projection["remediation_run_id"] is None


def test_projection_approved_carries_remediation_run_id_after_rerun() -> None:
    tenant_id = uuid4()
    proposal_id = uuid4()
    run_id = uuid4()
    cur = _FakeCursor(
        _one(_proposal_row()),  # proposal
        [("approve",)],  # latest decision
        [(run_id,)],  # lineage link -> remediation_run_id
    )
    projection = proposal_projection(cur, proposal_id, tenant_id)
    assert projection["status"] == "approved"
    assert projection["remediation_run_id"] == str(run_id)


def test_projection_rejected_from_reject_decision() -> None:
    tenant_id = uuid4()
    proposal_id = uuid4()
    cur = _FakeCursor(
        _one(_proposal_row()),  # proposal
        [("reject",)],  # latest decision
    )
    projection = proposal_projection(cur, proposal_id, tenant_id)
    assert projection["status"] == "rejected"
    assert projection["remediation_run_id"] is None


def test_projection_closed_from_close_decision() -> None:
    tenant_id = uuid4()
    proposal_id = uuid4()
    cur = _FakeCursor(
        _one(
            (
                *(_proposal_row()[:3]),
                "escalate-human",  # escalated proposal -> close is terminal
                *(_proposal_row()[4:]),
            )
        ),
        [("close",)],  # latest decision
    )
    projection = proposal_projection(cur, proposal_id, tenant_id)
    assert projection["status"] == "closed"
    assert projection["action"] == "escalate-human"
    assert projection["remediation_run_id"] is None