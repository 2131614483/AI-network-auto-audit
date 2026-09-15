"""M6 chain-level execution runs: preflight -> running -> success | failed.

One run is a single isolated read-only execution of a release-locked chain
under its own ``run_id``; every per-node ledger row is grouped by that
``run_id`` so the same chain can be re-drilled with a fresh idempotency key
(the M5 single-run ``already_executed`` semantic is retired).

``topology.execution_runs`` is a control surface (SELECT/INSERT/UPDATE, never
DELETE): state advances only by CAS ``running -> success|failed`` and every
mutation is tenant-scoped behind RLS.  Runs on the same chain are exclusive -
a second ``running`` run is rejected with 409, and the terminal state can only
be written once (the partial unique index on non-terminal rows is the hard
backstop behind the in-transaction check).

This module owns the fail-closed preflight, the run row lifecycle and the run
query projections; the service layer owns connections, Policy gateway
decisions and idempotency recording.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .contracts import validate_run
from .executor import gate_blockers

RUN_STATUSES = ("running", "success", "failed")
_ISOLATED_MODE = "isolated"


class RunPreflightError(PermissionError):
    """Fail-closed preflight denied the run; no child process ever started."""


class RunConflictError(ValueError):
    """Another isolated run is already running for the same chain (409)."""


@dataclass(frozen=True, slots=True)
class ChainContext:
    """Everything the run orchestration needs about the locked chain."""

    chain_id: UUID
    chain_key: str
    chain_checksum: str
    planner_version: str
    release_locked: bool
    nodes: list[dict[str, Any]]
    intents: list[dict[str, Any]]
    port_bindings: list[dict[str, Any]]

    @property
    def node_total(self) -> int:
        return len(self.nodes)


def load_chain_context(cur: Any, tenant_id: UUID, chain_key: str) -> ChainContext:
    """Load the release-locked chain plus its intents/nodes wiring (no execution)."""
    cur.execute(
        """SELECT id, chain_checksum, planner_version, chain_json
        FROM topology.invocation_chains WHERE tenant_id=%s AND chain_key=%s""",
        (tenant_id, chain_key),
    )
    chain_row = cur.fetchone()
    if chain_row is None:
        raise ValueError(f"chain not found: {chain_key}")
    chain_id, chain_checksum, planner_version, chain_json = chain_row
    payload = chain_json if isinstance(chain_json, dict) else {}
    cur.execute(
        """SELECT plan_node_slot_key, capability, intent_json, policy_decision, status
        FROM topology.invocation_intents WHERE tenant_id=%s AND chain_id=%s
        ORDER BY plan_node_slot_key""",
        (tenant_id, chain_id),
    )
    intents: list[dict[str, Any]] = []
    for slot_key, capability, intent_json, decision, status in cur.fetchall():
        projection = intent_json if isinstance(intent_json, dict) else {}
        # DB columns are current truth and win over the materialize-time snapshot.
        intents.append(
            {
                **projection,
                "slot_key": str(slot_key),
                "capability": str(capability),
                "policy_decision": str(decision),
                "status": str(status),
            }
        )
    cur.execute(
        """SELECT plan_node_slot_key, ordinal, expected_output
        FROM topology.invocation_chain_nodes WHERE tenant_id=%s AND chain_id=%s
        ORDER BY ordinal""",
        (tenant_id, chain_id),
    )
    nodes = [
        {
            "slot_key": str(slot_key),
            "ordinal": int(ordinal),
            "expected_output": [
                str(item) for item in (expected_output if isinstance(expected_output, (list, tuple)) else [])
            ],
        }
        for slot_key, ordinal, expected_output in cur.fetchall()
    ]
    port_bindings = [
        dict(binding) for binding in (payload.get("port_bindings") or []) if isinstance(binding, dict)
    ]
    return ChainContext(
        chain_id=UUID(str(chain_id)),
        chain_key=chain_key,
        chain_checksum=str(chain_checksum),
        planner_version=str(planner_version or "1.0.0"),
        release_locked=bool(payload.get("release_locked")),
        nodes=nodes,
        intents=intents,
        port_bindings=port_bindings,
    )


def preflight(context: ChainContext) -> None:
    """Fail-closed gate: release lock, intent blockers and non-empty nodes.

    The double Policy permission (``topology.chain.execute`` and
    ``topology.chain.execute.isolated``) is re-granted by the service before
    this module runs; the ISO gate inside the executor re-checks the
    verified-plugin / read_only / isolated_subprocess projection per node.
    """
    if not context.release_locked:
        raise RunPreflightError("chain is not release-locked; run denied")
    blockers = gate_blockers(context.intents)
    if blockers:
        raise RunPreflightError(f"chain run fail-closed: {blockers}")
    if context.node_total == 0:
        raise ValueError("chain has no nodes; cannot run")


def begin_run(
    cur: Any,
    *,
    tenant_id: UUID,
    context: ChainContext,
    reason: str,
    idempotency_key: str,
    trace_id: str,
) -> UUID:
    """Create one ``running`` run row after the same-chain concurrency check.

    A second run for the same chain while an earlier run is still ``running``
    is rejected with :class:`RunConflictError` (deduplicated to HTTP 409).
    """
    cur.execute(
        """SELECT id FROM topology.execution_runs
        WHERE tenant_id=%s AND chain_id=%s AND status='running' LIMIT 1""",
        (tenant_id, context.chain_id),
    )
    if cur.fetchone() is not None:
        raise RunConflictError("another isolated run is already running for this chain")
    cur.execute(
        """INSERT INTO topology.execution_runs
        (tenant_id, chain_id, chain_key, mode, status, reason, idempotency_key, trace_id,
         node_total, chain_checksum, planner_version)
        VALUES(%s,%s,%s,%s,'running',%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            tenant_id,
            context.chain_id,
            context.chain_key,
            _ISOLATED_MODE,
            reason,
            idempotency_key,
            trace_id,
            context.node_total,
            context.chain_checksum,
            context.planner_version,
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("execution run insert returned no id")
    return UUID(str(row[0]))


def finalize_run(
    cur: Any,
    *,
    run_id: UUID,
    tenant_id: UUID,
    status: str,
    node_succeeded: int,
    node_failed: int,
) -> None:
    """CAS transition ``running -> success|failed``; never re-advances a closed run."""
    if status not in ("success", "failed"):
        raise ValueError(f"invalid terminal run status: {status}")
    cur.execute(
        """UPDATE topology.execution_runs
        SET status=%s, node_succeeded=%s, node_failed=%s, finished_at=now()
        WHERE id=%s AND tenant_id=%s AND status='running'""",
        (status, node_succeeded, node_failed, run_id, tenant_id),
    )
    if cur.rowcount != 1:
        raise RunConflictError("run is not in 'running' state; cannot finalize")


def run_projection(cur: Any, run_id: UUID, tenant_id: UUID) -> dict[str, Any]:
    """Strict schema-valid projection of one run (no ledger payload body)."""
    cur.execute(
        """SELECT chain_key, mode, status, reason, node_total, node_succeeded, node_failed,
                  chain_checksum, planner_version, trace_id, started_at, finished_at
        FROM topology.execution_runs WHERE id=%s AND tenant_id=%s""",
        (run_id, tenant_id),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"run not found: {run_id}")
    (
        chain_key,
        mode,
        status,
        reason,
        node_total,
        node_succeeded,
        node_failed,
        chain_checksum,
        planner_version,
        trace_id,
        started_at,
        finished_at,
    ) = row
    projection: dict[str, Any] = {
        "run_id": str(run_id),
        "chain_key": str(chain_key),
        "mode": str(mode),
        "status": str(status),
        "node_total": int(node_total),
        "node_succeeded": int(node_succeeded),
        "node_failed": int(node_failed),
        "reason": str(reason),
        "trace_id": str(trace_id),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat() if finished_at else None,
        "chain_checksum": str(chain_checksum),
        "planner_version": str(planner_version),
    }
    validate_run(projection)
    return projection


def list_run_rows(cur: Any, tenant_id: UUID, chain_key: str, limit: int = 50) -> list[dict[str, Any]]:
    """Run-list projections (newest first) for a chain; detail is per-run."""
    cur.execute(
        """SELECT r.id, c.chain_key, r.mode, r.status, r.node_total, r.node_succeeded,
                  r.node_failed, r.reason, r.trace_id, r.started_at, r.finished_at,
                  r.chain_checksum, r.planner_version
        FROM topology.execution_runs r
        JOIN topology.invocation_chains c ON c.id=r.chain_id AND c.tenant_id=r.tenant_id
        WHERE r.tenant_id=%s AND c.chain_key=%s
        ORDER BY r.created_at DESC, r.id LIMIT %s""",
        (tenant_id, chain_key, limit),
    )
    rows: list[dict[str, Any]] = []
    for (
        run_id,
        row_chain_key,
        mode,
        status,
        node_total,
        node_succeeded,
        node_failed,
        reason,
        trace_id,
        started_at,
        finished_at,
        chain_checksum,
        planner_version,
    ) in cur.fetchall():
        projection = {
            "run_id": str(run_id),
            "chain_key": str(row_chain_key),
            "mode": str(mode),
            "status": str(status),
            "node_total": int(node_total),
            "node_succeeded": int(node_succeeded),
            "node_failed": int(node_failed),
            "reason": str(reason),
            "trace_id": str(trace_id),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat() if finished_at else None,
            "chain_checksum": str(chain_checksum),
            "planner_version": str(planner_version),
        }
        validate_run(projection)
        rows.append(projection)
    return rows


def run_entries(cur: Any, tenant_id: UUID, run_id: UUID, limit: int = 500) -> list[dict[str, Any]]:
    """The per-run ledger grouping: every node row recorded under this run_id."""
    cur.execute(
        """SELECT e.id, e.plan_node_slot_key, e.ordinal, e.mode, e.input_refs, e.output_refs,
                  e.output_contract, e.output_checksum, e.status, e.policy_ref, e.trace_id,
                  e.started_at, e.finished_at, e.plugin_id, e.plugin_version,
                  e.runtime_code_sha256, e.input_sha256, e.output_artifact_refs
        FROM topology.execution_ledger e
        WHERE e.tenant_id=%s AND e.run_id=%s
        ORDER BY e.ordinal LIMIT %s""",
        (tenant_id, run_id, limit),
    )
    entries: list[dict[str, Any]] = []
    for (
        row_id,
        slot_key,
        ordinal,
        mode,
        input_refs,
        output_refs,
        output_contract,
        output_checksum,
        status,
        policy_ref,
        trace_id,
        started_at,
        finished_at,
        plugin_id,
        plugin_version,
        runtime_code_sha256,
        input_sha256,
        output_artifact_refs,
    ) in cur.fetchall():
        entries.append(
            {
                "execution_id": "exec-" + hashlib.sha256(str(row_id).encode("utf-8")).hexdigest()[:16],
                "slot_key": str(slot_key),
                "ordinal": int(ordinal),
                "mode": str(mode),
                "input_refs": input_refs if isinstance(input_refs, (list, tuple)) else [],
                "output_refs": output_refs if isinstance(output_refs, (list, tuple)) else [],
                "output_contract": str(output_contract or ""),
                "output_checksum": str(output_checksum),
                "status": str(status),
                "policy_ref": str(policy_ref or ""),
                "trace_id": str(trace_id),
                "started_at": started_at.isoformat() if started_at else None,
                "finished_at": finished_at.isoformat() if finished_at else None,
                "plugin_id": str(plugin_id or ""),
                "plugin_version": str(plugin_version or ""),
                "runtime_code_sha256": str(runtime_code_sha256 or ""),
                "input_sha256": str(input_sha256 or ""),
                "output_artifact_refs": (
                    output_artifact_refs if isinstance(output_artifact_refs, (list, tuple)) else []
                ),
            }
        )
    return entries