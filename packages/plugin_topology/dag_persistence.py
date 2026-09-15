"""CW3: per-node attempt persistence — short-transaction node commits.

Every node write is a short transaction on ``control.node_attempts``:
begin (INSERT running + lease token) -> execute outside the DB -> finish
(fencing-token UPDATE + outbox event).  A stale worker's late write-back is
rejected (``FencingError``) and traced via an outbox event; repeat submits
read back the same attempt; recovery marks lease-expired attempts failed;
retry creates a new attempt_seq while the logical node identity stays stable
(方案 7.2 短事务、恢复与幂等 / CW3 退出条件).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg2

logger = logging.getLogger("plugin_topology.dag_persistence")


class FencingError(RuntimeError):
    """A stale worker tried to write an attempt it no longer owns."""


class AttemptNotFoundError(RuntimeError):
    """The attempt row does not exist for this tenant."""


class AttemptStore:
    """Short-transaction node attempt ledger over one database."""

    def __init__(self, database_url: str, *, worker_id: str | None = None) -> None:
        self.database_url = database_url
        self.worker_id = worker_id or f"worker-{uuid4().hex[:8]}"

    # -- short-transaction writes ---------------------------------------------

    def begin_attempt(
        self,
        *,
        tenant_id: UUID,
        run_id: str,
        plan_key: str,
        execution_hash: str,
        node_instance_id: str,
        capability: str,
        plugin_id: str,
        attempt_seq: int,
        trace_id: str,
        lease_seconds: int = 300,
    ) -> dict[str, Any]:
        """INSERT one running attempt (or read it back if already present)."""
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.fetchone()
            cur.execute(
                """SELECT attempt_id, status, lease_token FROM control.node_attempts
                WHERE tenant_id=%s AND run_id=%s AND node_instance_id=%s AND attempt_seq=%s""",
                (str(tenant_id), run_id, node_instance_id, attempt_seq),
            )
            existing = cur.fetchone()
            if existing is not None:
                return {
                    "attempt_id": str(existing[0]), "status": str(existing[1]),
                    "lease_token": str(existing[2]) if existing[2] else None,
                    "attempt_seq": attempt_seq,
                    "idempotent": True,
                }
            attempt_id = uuid4()
            lease_token = uuid4()
            cur.execute(
                """INSERT INTO control.node_attempts
                   (attempt_id, tenant_id, run_id, plan_key, execution_hash,
                    node_instance_id, capability, plugin_id, attempt_seq, status,
                    lease_token, lease_expires_at, worker_id, trace_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'running',%s,%s,%s,%s)""",
                (
                    str(attempt_id), str(tenant_id), run_id, plan_key, execution_hash,
                    node_instance_id, capability, plugin_id, attempt_seq,
                    str(lease_token),
                    (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(),
                    self.worker_id, trace_id,
                ),
            )
            connection.commit()
            return {
                "attempt_id": str(attempt_id), "status": "running",
                "lease_token": str(lease_token), "attempt_seq": attempt_seq,
                "idempotent": False,
            }

    def reclaim_attempt(
        self,
        *,
        tenant_id: UUID,
        attempt_id: str,
        lease_seconds: int = 300,
    ) -> dict[str, Any]:
        """A fresh worker reclaims an expired-running attempt (new fencing token).

        Only rows still ``running`` with an expired lease may be reclaimed;
        the new token becomes the only valid one for the terminal write.
        """
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.fetchone()
            new_token = uuid4()
            cur.execute(
                """UPDATE control.node_attempts
                   SET lease_token=%s, lease_expires_at=%s, worker_id=%s
                   WHERE tenant_id=%s AND attempt_id=%s AND status='running'
                     AND lease_expires_at < now()""",
                (
                    str(new_token),
                    (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(),
                    self.worker_id, str(tenant_id), attempt_id,
                ),
            )
            connection.commit()
            if cur.rowcount == 0:
                raise FencingError(f"attempt {attempt_id} is not reclaimable (not running/expired)")
            return {"attempt_id": attempt_id, "lease_token": str(new_token)}

    def renew_attempt_lease(
        self,
        *,
        tenant_id: UUID,
        attempt_id: str,
        expected_lease_token: str,
        lease_seconds: int = 300,
    ) -> None:
        """Extend the lease; fencing: only the current token owner may renew."""
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.fetchone()
            cur.execute(
                """UPDATE control.node_attempts SET lease_expires_at=%s, worker_id=%s
                WHERE tenant_id=%s AND attempt_id=%s AND lease_token=%s""",
                (
                    (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(),
                    self.worker_id, str(tenant_id), attempt_id, expected_lease_token,
                ),
            )
            connection.commit()

    def finish_attempt(
        self,
        *,
        tenant_id: UUID,
        attempt_id: str,
        expected_lease_token: str,
        status: str,
        input_bindings: dict[str, Any] | None = None,
        output_refs: dict[str, Any] | None = None,
        error_kind: str | None = None,
        error_message: str | None = None,
        plan_key: str | None = None,
        execution_hash: str | None = None,
        plugin_version: str | None = None,
        runtime_code_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Fencing-token terminal write + ``node.finished`` outbox event.

        ``plugin_version`` / ``runtime_code_sha256`` record which implementation
        actually ran, matching what the M6 isolated surface
        (``topology.execution_ledger``) has stored since 0041.  A node that
        failed before reaching the runtime passes neither, and the columns keep
        their ``''`` default rather than a fabricated value.
        """
        if status not in {"succeeded", "failed", "retry_wait", "cancelled"}:
            raise ValueError(f"invalid terminal status: {status}")
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.fetchone()
            cur.execute(
                """SELECT lease_token, status, plan_key, execution_hash
                FROM control.node_attempts
                WHERE tenant_id=%s AND attempt_id=%s""",
                (str(tenant_id), attempt_id),
            )
            row = cur.fetchone()
            if row is None:
                raise AttemptNotFoundError(f"attempt {attempt_id} not found")
            current_token, current_status, row_plan, row_hash = row
            if str(current_token) != str(expected_lease_token):
                cur.execute(
                    "SELECT event.enqueue_outbox(%s,'control.dag','node.finish.rejected',%s,%s)",
                    (
                        str(tenant_id), attempt_id,
                        psycopg2.extras.Json({
                            "attempt_id": attempt_id,
                            "current_status": current_status,
                            "rejected_lease_token": expected_lease_token,
                            "rejected_at": datetime.now(timezone.utc).isoformat(),
                        }),
                    ),
                )
                connection.commit()
                raise FencingError(
                    f"stale worker write rejected for attempt {attempt_id}: "
                    f"lease token mismatch"
                )
            cur.execute(
                """UPDATE control.node_attempts
                   SET status=%s, input_bindings=%s, output_refs=%s,
                       error_kind=%s, error_message=%s, finished_at=now(),
                       plan_key=COALESCE(%s, plan_key),
                       execution_hash=COALESCE(%s, execution_hash),
                       plugin_version=COALESCE(%s, plugin_version),
                       runtime_code_sha256=COALESCE(%s, runtime_code_sha256)
                   WHERE tenant_id=%s AND attempt_id=%s""",
                (
                    status,
                    psycopg2.extras.Json(input_bindings or {}),
                    psycopg2.extras.Json(output_refs or {}),
                    error_kind, error_message, plan_key, execution_hash,
                    plugin_version, runtime_code_sha256,
                    str(tenant_id), attempt_id,
                ),
            )
            cur.execute(
                "SELECT event.enqueue_outbox(%s,'control.dag','node.finished',%s,%s)",
                (
                    str(tenant_id), attempt_id,
                    psycopg2.extras.Json({
                        "attempt_id": attempt_id,
                        "status": status,
                        "error_kind": error_kind,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }),
                ),
            )
            connection.commit()
            return {"attempt_id": attempt_id, "status": status}

    # -- recovery / retry ------------------------------------------------------

    def retry_attempt(
        self,
        *,
        tenant_id: UUID,
        run_id: str,
        node_instance_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        """Create the next attempt_seq for the same logical node (identity stable)."""
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.fetchone()
            cur.execute(
                """SELECT plan_key, execution_hash, capability, plugin_id,
                          COALESCE(MAX(attempt_seq), 0)
                   FROM control.node_attempts
                   WHERE tenant_id=%s AND run_id=%s AND node_instance_id=%s
                   GROUP BY plan_key, execution_hash, capability, plugin_id""",
                (str(tenant_id), run_id, node_instance_id),
            )
            row = cur.fetchone()
            if row is None:
                raise AttemptNotFoundError(f"node {node_instance_id} has no attempt yet")
            plan_key, execution_hash, capability, plugin_id, max_seq = row
            new_seq = int(max_seq) + 1
        return self.begin_attempt(
            tenant_id=tenant_id, run_id=run_id, plan_key=plan_key,
            execution_hash=execution_hash, node_instance_id=node_instance_id,
            capability=capability, plugin_id=plugin_id, attempt_seq=new_seq,
            trace_id=trace_id,
        )

    # -- traceability queries --------------------------------------------------

    def query_attempts(self, *, tenant_id: UUID, run_id: str) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.fetchone()
            cur.execute(
                """SELECT attempt_id, run_id, plan_key, execution_hash,
                          node_instance_id, capability, plugin_id,
                          attempt_seq, status, lease_token, worker_id,
                          input_bindings, output_refs, error_kind, error_message,
                          trace_id, created_at, finished_at,
                          plugin_version, runtime_code_sha256
                   FROM control.node_attempts
                   WHERE tenant_id=%s AND run_id=%s
                   ORDER BY node_instance_id, attempt_seq""",
                (str(tenant_id), run_id),
            )
            return [
                {
                    "attempt_id": str(row[0]), "run_id": str(row[1]),
                    "plan_key": row[2], "execution_hash": row[3],
                    "node_instance_id": row[4],
                    "capability": row[5], "plugin_id": row[6], "attempt_seq": row[7],
                    "status": row[8], "lease_token": str(row[9]) if row[9] else None,
                    "worker_id": row[10],
                    "input_bindings": row[11] if isinstance(row[11], dict) else {},
                    "output_refs": row[12] if isinstance(row[12], dict) else {},
                    "error_kind": row[13], "error_message": row[14],
                    "trace_id": row[15],
                    "created_at": row[16].isoformat() if row[16] else None,
                    "finished_at": row[17].isoformat() if row[17] else None,
                    "plugin_version": row[18] or "",
                    "runtime_code_sha256": row[19] or "",
                }
                for row in cur.fetchall()
            ]

    def query_edges(self, *, tenant_id: UUID, run_id: str) -> list[dict[str, Any]]:
        """Expand every port hand-off into a data-edge row (方案 :454 数据流)."""
        edges: list[dict[str, Any]] = []
        for attempt in self.query_attempts(tenant_id=tenant_id, run_id=run_id):
            for port_id, binding in (attempt["input_bindings"] or {}).items():
                if not isinstance(binding, dict):
                    continue
                edges.append({
                    "key": (
                        binding.get("source_instance"),
                        binding.get("source_port"),
                        attempt["node_instance_id"],
                        port_id,
                    ),
                    "source_instance": binding.get("source_instance"),
                    "source_port": binding.get("source_port"),
                    "target_instance": attempt["node_instance_id"],
                    "target_port": port_id,
                    "sha256": binding.get("sha256"),
                    "uri": binding.get("uri"),
                    "adapter": binding.get("adapter"),
                    "attempt_id": attempt["attempt_id"],
                })
        return edges


def recover_and_retry_once(
    database_url: str,
    *,
    tenant_id: UUID,
    worker_id: str | None = None,
    stale_before_seconds: int = 0,
) -> dict[str, int]:
    """Background sweep: mark lease-expired running attempts failed (traced).

    The retry of ``retry_wait`` / recovered attempts is explicit (a caller
    decides policy); this sweep only guarantees a crashed worker's attempt is
    never left running forever, and the transition is an outbox event.
    """
    sweeper = AttemptStore(database_url, worker_id=worker_id)
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            """SELECT attempt_id, lease_token, plan_key, execution_hash, run_id,
                      node_instance_id, capability, plugin_id, attempt_seq, trace_id
               FROM control.node_attempts
               WHERE tenant_id=%s AND status='running'
                 AND lease_expires_at < now() - make_interval(secs => %s)
               ORDER BY lease_expires_at
               LIMIT 50""",
            (str(tenant_id), max(0, stale_before_seconds)),
        )
        stale = cur.fetchall()
        for attempt_id, lease_token, plan_key, execution_hash, run_id, node, cap, plugin, seq, trace in stale:
            cur.execute(
                """UPDATE control.node_attempts
                   SET status='failed', error_kind='lease_expired',
                       error_message='worker lease expired; recovered by sweeper',
                       finished_at=now()
                   WHERE tenant_id=%s AND attempt_id=%s AND lease_token=%s
                     AND status='running'""",
                (str(tenant_id), str(attempt_id), str(lease_token)),
            )
            cur.execute(
                "SELECT event.enqueue_outbox(%s,'control.dag','node.lease.expired',%s,%s)",
                (
                    str(tenant_id), str(attempt_id),
                    psycopg2.extras.Json({
                        "attempt_id": str(attempt_id), "run_id": str(run_id),
                        "node_instance_id": node, "attempt_seq": seq,
                        "error_kind": "lease_expired",
                        "recovered_by": sweeper.worker_id,
                    }),
                ),
            )
        connection.commit()
        return {"recovered": len(stale), "retried": 0}
