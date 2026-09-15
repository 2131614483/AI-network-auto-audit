"""Plugin Topology M6 unit tests: run preflight, run-row lifecycle (CAS),
and the same-chain concurrency conflict.

Nothing here touches a database or starts a child process: the preflight is a
pure function over a fabricated ChainContext and the cursor is a recording
fake, so the run state machine and fail-closed surfaces are proven in
isolation (the real DB path is covered by the integration layer).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from packages.plugin_topology.runs import (
    ChainContext,
    RunConflictError,
    RunPreflightError,
    begin_run,
    finalize_run,
    preflight,
)


def _context(**overrides: Any) -> ChainContext:
    values: dict[str, Any] = {
        "chain_id": uuid4(),
        "chain_key": "chain-" + "a" * 16,
        "chain_checksum": "a" * 64,
        "planner_version": "1.0.0",
        "release_locked": True,
        "nodes": [{"slot_key": "s.one", "ordinal": 0, "expected_output": ["out.one"]}],
        "intents": [{"slot_key": "s.one", "policy_decision": "allowed", "status": "policy_allowed"}],
        "port_bindings": [],
    }
    values.update(overrides)
    return ChainContext(**values)


def _tenant() -> UUID:
    return uuid4()


class _FakeCur:
    """Recording cursor: a running row, an INSERT RETURNING id, or CAS rowcount."""

    def __init__(self, *, running_exists: bool = False, inserted_id: UUID | None = None, rowcount: int = 1) -> None:
        self.running_exists = running_exists
        self.inserted_id = inserted_id or uuid4()
        self.rowcount = rowcount
        self.script: list[str] = []
        self.last_sql = ""
        self.last_params: tuple[Any, ...] = ()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:  # noqa: ANN001
        self.script.append(sql)
        self.last_sql = sql
        self.last_params = params

    def fetchone(self) -> tuple[Any, ...] | None:
        if "INSERT INTO topology.execution_runs" in self.last_sql:
            return (self.inserted_id,)
        return (self.inserted_id,) if self.running_exists else None


# -- preflight (fail-closed) ---------------------------------------------------


def test_preflight_accepts_release_locked_gated_chain() -> None:
    preflight(_context())
    # no exception means the gate passed


def test_preflight_rejects_unlocked_chain() -> None:
    with pytest.raises(RunPreflightError, match="not release-locked"):
        preflight(_context(release_locked=False))


def test_preflight_rejects_denied_intent() -> None:
    with pytest.raises(RunPreflightError, match="fail-closed"):
        preflight(
            _context(intents=[{"slot_key": "s.one", "policy_decision": "denied", "status": "denied"}])
        )


def test_preflight_rejects_pending_approval() -> None:
    with pytest.raises(RunPreflightError, match="approval pending"):
        preflight(
            _context(
                intents=[
                    {
                        "slot_key": "s.one",
                        "policy_decision": "requires_approval",
                        "status": "materialized",
                    }
                ]
            )
        )


def test_preflight_rejects_chain_without_nodes() -> None:
    with pytest.raises(ValueError, match="no nodes"):
        preflight(_context(nodes=[]))


def test_preflight_error_is_a_permission_error() -> None:
    with pytest.raises(PermissionError):
        preflight(_context(release_locked=False))


# -- begin_run: create the running row (409 on a second run) -------------------


def test_begin_run_creates_running_row() -> None:
    cur = _FakeCur(inserted_id=uuid4())
    run_id = begin_run(
        cur,
        tenant_id=_tenant(),
        context=_context(),
        reason="M6 受限只读演练",
        idempotency_key="unit-1",
        trace_id=str(uuid4()),
    )
    assert run_id == cur.inserted_id
    assert any("status='running'" in sql for sql in cur.script)
    insert_sql = cur.script[-1]
    assert "INSERT INTO topology.execution_runs" in insert_sql
    assert "RETURNING id" in insert_sql


def test_begin_run_rejects_second_running_run() -> None:
    cur = _FakeCur(running_exists=True)
    with pytest.raises(RunConflictError, match="already running"):
        begin_run(
            cur,
            tenant_id=_tenant(),
            context=_context(),
            reason="M6 并发演练",
            idempotency_key="unit-2",
            trace_id=str(uuid4()),
        )


# -- finalize_run: CAS running -> success | failed -----------------------------


def test_finalize_run_cas_advances_running_to_success() -> None:
    cur = _FakeCur(rowcount=1)
    finalize_run(
        cur,
        run_id=uuid4(),
        tenant_id=_tenant(),
        status="success",
        node_succeeded=2,
        node_failed=0,
    )
    update_sql = cur.script[-1]
    assert "UPDATE topology.execution_runs" in update_sql
    assert "SET status=%s, node_succeeded=%s, node_failed=%s, finished_at=now()" in update_sql
    assert "AND status='running'" in update_sql  # CAS guard: never re-advance a closed run
    assert cur.last_params[:3] == ("success", 2, 0)


def test_finalize_run_rejects_invalid_terminal_status() -> None:
    cur = _FakeCur(rowcount=1)
    with pytest.raises(ValueError, match="invalid terminal run status"):
        finalize_run(
            cur,
            run_id=uuid4(),
            tenant_id=_tenant(),
            status="queued",
            node_succeeded=0,
            node_failed=0,
        )
    assert cur.script == []  # nothing was executed


def test_finalize_run_cas_failure_raises_conflict() -> None:
    cur = _FakeCur(rowcount=0)  # the row was already finalized or tampered
    with pytest.raises(RunConflictError, match="not in 'running' state"):
        finalize_run(
            cur,
            run_id=uuid4(),
            tenant_id=_tenant(),
            status="failed",
            node_succeeded=0,
            node_failed=2,
        )


def test_node_total_matches_nodes() -> None:
    assert _context().node_total == 1
    assert _context(nodes=_context().nodes * 3).node_total == 3