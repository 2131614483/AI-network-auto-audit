"""Minimal durable Mission/DAG/TaskRun/AgentRun scheduler.

The first vertical slice deliberately executes only handlers registered by the
embedding process.  It never interprets a plugin entrypoint as a shell command,
imports arbitrary module paths, or starts a child process.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import Json, register_uuid

from packages.policy.engine import PolicyEngine

register_uuid()  # type: ignore[no-untyped-call]


PluginHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class TaskInfo:
    id: UUID
    node_key: str
    capability: str
    status: str
    attempt: int
    max_attempts: int
    trace_id: str


@dataclass(frozen=True, slots=True)
class AgentRunInfo:
    id: UUID
    task_run_id: UUID
    status: str


def _checksum(graph: Mapping[str, Any]) -> str:
    payload = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalise_graph(graph: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_nodes = graph.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("workflow graph must contain a non-empty nodes list")
    nodes: list[dict[str, Any]] = []
    keys: set[str] = set()
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise ValueError("workflow nodes must be objects")
        key = raw.get("key")
        capability = raw.get("capability")
        if not isinstance(key, str) or not key or key in keys:
            raise ValueError("workflow node keys must be unique non-empty strings")
        if not isinstance(capability, str) or not capability.strip():
            raise ValueError(f"workflow node {key!r} requires a capability")
        dependencies = raw.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
            raise ValueError(f"workflow node {key!r} has invalid depends_on")
        keys.add(key)
        nodes.append(
            {
                "key": key,
                "capability": capability,
                "depends_on": list(dict.fromkeys(dependencies)),
                "max_attempts": int(raw.get("max_attempts", 1)),
                "priority": int(raw.get("priority", 100)),
            }
        )
    for node in nodes:
        if node["max_attempts"] < 1 or node["max_attempts"] > 20:
            raise ValueError(f"workflow node {node['key']!r} max_attempts must be 1..20")
        unknown = set(node["depends_on"]) - keys
        if unknown:
            raise ValueError(f"workflow node {node['key']!r} depends on unknown nodes: {sorted(unknown)}")
    # Kahn's algorithm rejects cycles before anything is persisted.
    remaining = {node["key"]: set(node["depends_on"]) for node in nodes}
    resolved: set[str] = set()
    while remaining:
        ready = {key for key, deps in remaining.items() if deps <= resolved}
        if not ready:
            raise ValueError("workflow graph contains a cycle")
        resolved.update(ready)
        for key in ready:
            del remaining[key]
    return nodes


class Scheduler:
    """Tenant-scoped scheduler backed by PostgreSQL and RLS."""

    def __init__(self, database_url: str, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.tenant_slug = tenant_slug

    @staticmethod
    def _enqueue_task_ready(cur: Any, tenant_id: UUID, task_run_id: UUID, capability: str) -> None:
        """Publish a durable scheduling hint in the same transaction as readiness.

        The event is a hint, not authority: a consumer must still atomically
        claim the task and pass its policy gate.  This makes redelivery safe.
        """
        cur.execute(
            "SELECT event.enqueue_outbox(%s,'control.scheduler','task.ready',%s,%s)",
            (tenant_id, task_run_id, Json({"task_run_id": str(task_run_id), "capability": capability})),
        )

    @staticmethod
    def _tenant(cur: Any, slug: str) -> UUID:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {slug}")
        tenant_id: UUID = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return tenant_id

    def create_workflow(
        self, key: str, name: str, version: str, graph: Mapping[str, Any], created_by: UUID | None = None
    ) -> UUID:
        nodes = _normalise_graph(graph)
        canonical_graph = {"nodes": nodes}
        checksum = _checksum(canonical_graph)
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                cur.execute(
                    """INSERT INTO control.workflow_definitions(tenant_id,key,name)
                    VALUES(%s,%s,%s)
                    ON CONFLICT(tenant_id,key) DO UPDATE SET name=EXCLUDED.name
                    RETURNING id""",
                    (tenant_id, key, name),
                )
                workflow_row = cur.fetchone()
                if workflow_row is None:
                    raise RuntimeError("workflow definition upsert returned no id")
                workflow_id = UUID(str(workflow_row[0]))
                cur.execute(
                    """SELECT id,checksum FROM control.workflow_versions
                     WHERE tenant_id=%s AND workflow_id=%s AND version=%s""",
                    (tenant_id, workflow_id, version),
                )
                existing = cur.fetchone()
                if existing is not None:
                    if existing[1] != checksum:
                        raise ValueError("published workflow version is immutable")
                    return UUID(str(existing[0]))
                cur.execute(
                    """INSERT INTO control.workflow_versions
                    (tenant_id,workflow_id,version,graph_json,checksum,created_by)
                    VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (tenant_id, workflow_id, version, Json(canonical_graph), checksum, created_by),
                )
                version_row = cur.fetchone()
                if version_row is None:
                    raise RuntimeError("workflow version insert returned no id")
                return UUID(str(version_row[0]))

    def create_mission(
        self,
        title: str,
        objective: str,
        domain: str,
        project_slug: str = "default",
        autonomy_mode: str = "approval_required",
        requested_by: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> UUID:
        if autonomy_mode not in {"approval_required", "auto_low_risk", "manual"}:
            raise ValueError("invalid autonomy mode")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                cur.execute(
                    "SELECT id FROM iam.projects WHERE tenant_id=%s AND slug=%s",
                    (tenant_id, project_slug),
                )
                project = cur.fetchone()
                if project is None:
                    raise ValueError(f"project not found: {project_slug}")
                request_hash = hashlib.sha256(
                    json.dumps(
                        {"title": title, "objective": objective, "domain": domain,
                         "project_slug": project_slug, "autonomy_mode": autonomy_mode,
                         "requested_by": str(requested_by) if requested_by else None},
                        sort_keys=True, separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if idempotency_key is not None:
                    if not idempotency_key.strip():
                        raise ValueError("idempotency_key is required")
                    cur.execute(
                        "SELECT request_hash,response_json FROM control.idempotency_records "
                        "WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE",
                        (tenant_id, idempotency_key),
                    )
                    existing = cur.fetchone()
                    if existing is not None:
                        if existing[0] != request_hash:
                            raise ValueError("Idempotency-Key reused with different request")
                        if isinstance(existing[1], dict) and existing[1].get("mission_id"):
                            return UUID(str(existing[1]["mission_id"]))
                    else:
                        cur.execute(
                            "INSERT INTO control.idempotency_records(tenant_id,idempotency_key,request_hash) VALUES(%s,%s,%s)",
                            (tenant_id, idempotency_key, request_hash),
                        )
                cur.execute(
                    """INSERT INTO control.missions
                    (tenant_id,project_id,domain,title,objective,autonomy_mode,requested_by)
                    VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (tenant_id, project[0], domain, title, objective, autonomy_mode, requested_by),
                )
                mission_row = cur.fetchone()
                if mission_row is None:
                    raise RuntimeError("mission insert returned no id")
                mission_id = UUID(str(mission_row[0]))
                if idempotency_key is not None:
                    cur.execute(
                        "UPDATE control.idempotency_records SET response_status=201,response_json=%s "
                        "WHERE tenant_id=%s AND idempotency_key=%s",
                        (Json({"mission_id": str(mission_id)}), tenant_id, idempotency_key),
                    )
                return mission_id

    def start_run(
        self,
        mission_id: UUID,
        workflow_key: str,
        workflow_version: str,
        input_payload: Mapping[str, Any] | None,
        idempotency_key: str,
        trace_id: UUID | None = None,
    ) -> UUID:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        graph_input = input_payload or {}
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                cur.execute(
                    "SELECT id,project_id FROM control.missions WHERE tenant_id=%s AND id=%s",
                    (tenant_id, mission_id),
                )
                mission = cur.fetchone()
                if mission is None:
                    raise ValueError("mission not found")
                cur.execute(
                    """SELECT wv.id,wv.graph_json FROM control.workflow_versions wv
                    JOIN control.workflow_definitions wd ON wd.id=wv.workflow_id AND wd.tenant_id=wv.tenant_id
                    WHERE wv.tenant_id=%s AND wd.key=%s AND wv.version=%s AND wv.status='published'""",
                    (tenant_id, workflow_key, workflow_version),
                )
                workflow = cur.fetchone()
                if workflow is None:
                    raise ValueError("published workflow version not found")
                cur.execute(
                    "SELECT id FROM control.workflow_runs WHERE tenant_id=%s AND idempotency_key=%s",
                    (tenant_id, idempotency_key),
                )
                existing = cur.fetchone()
                if existing is not None:
                    return UUID(str(existing[0]))
                run_id = uuid4()
                cur.execute(
                    """INSERT INTO control.workflow_runs
                    (id,tenant_id,project_id,mission_id,workflow_version_id,workflow_key,workflow_version,
                     status,idempotency_key,input_payload,current_revision,trace_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,1,%s)""",
                    (run_id, tenant_id, mission[1], mission_id, workflow[0], workflow_key, workflow_version,
                     idempotency_key, Json(graph_input), str(trace_id or uuid4())),
                )
                nodes = _normalise_graph(workflow[1])
                task_ids: dict[str, UUID] = {}
                for node in nodes:
                    task_id = uuid4()
                    task_ids[node["key"]] = task_id
                    status = "ready" if not node["depends_on"] else "pending"
                    cur.execute(
                        """INSERT INTO control.task_runs
                        (id,tenant_id,project_id,workflow_run_id,node_key,capability,status,priority,attempt,max_attempts,
                         idempotency_key,input_payload,scheduled_at,trace_id)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s)""",
                        (task_id, tenant_id, mission[1], run_id, node["key"], node["capability"], status,
                         node["priority"], node["max_attempts"], f"{run_id}:{node['key']}", Json(graph_input),
                         datetime.now(timezone.utc) if status == "ready" else None, str(trace_id or uuid4())),
                    )
                    if status == "ready":
                        self._enqueue_task_ready(cur, tenant_id, task_id, node["capability"])
                for node in nodes:
                    for dependency in node["depends_on"]:
                        cur.execute(
                            "INSERT INTO control.task_dependencies(tenant_id,task_run_id,depends_on_task_run_id) VALUES(%s,%s,%s)",
                            (tenant_id, task_ids[node["key"]], task_ids[dependency]),
                        )
                return run_id

    def runnable_tasks(self, workflow_run_id: UUID) -> list[TaskInfo]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                cur.execute(
                    """UPDATE control.task_runs task SET status='ready',scheduled_at=now()
                    WHERE task.tenant_id=%s AND task.workflow_run_id=%s AND task.status='pending'
                      AND NOT EXISTS (
                        SELECT 1 FROM control.task_dependencies dep
                        JOIN control.task_runs prerequisite ON prerequisite.id=dep.depends_on_task_run_id
                        WHERE dep.tenant_id=%s AND dep.task_run_id=task.id AND prerequisite.status <> 'completed'
                      ) RETURNING id,capability""",
                    (tenant_id, workflow_run_id, tenant_id),
                )
                for task_id, capability in cur.fetchall():
                    self._enqueue_task_ready(cur, tenant_id, UUID(str(task_id)), str(capability))
                cur.execute(
                    """SELECT id,node_key,capability,status,attempt,max_attempts,trace_id
                    FROM control.task_runs WHERE tenant_id=%s AND workflow_run_id=%s AND status='ready'
                    ORDER BY priority DESC,created_at,id""",
                    (tenant_id, workflow_run_id),
                )
                return [TaskInfo(*row) for row in cur.fetchall()]

    def ready_tasks(self, limit: int = 5, workflow_run_id: UUID | None = None) -> list[TaskInfo]:
        """Ready tasks, optionally scoped to one workflow run.

        The task.ready outbox events are hints, so scanning readiness directly
        is safe and idempotent.  The worker uses the tenant-wide form; seeds and
        per-run drivers pass ``workflow_run_id`` to avoid cross-run claims.
        """
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                if workflow_run_id is None:
                    cur.execute(
                        """SELECT id,node_key,capability,status,attempt,max_attempts,trace_id
                        FROM control.task_runs WHERE tenant_id=%s AND status='ready'
                        ORDER BY priority DESC,created_at,id LIMIT %s""",
                        (tenant_id, limit),
                    )
                else:
                    cur.execute(
                        """SELECT id,node_key,capability,status,attempt,max_attempts,trace_id
                        FROM control.task_runs WHERE tenant_id=%s AND workflow_run_id=%s AND status='ready'
                        ORDER BY priority DESC,created_at,id LIMIT %s""",
                        (tenant_id, workflow_run_id, limit),
                    )
                return [TaskInfo(*row) for row in cur.fetchall()]

    def claim_task(
        self,
        task_run_id: UUID,
        role_key: str,
        model_key: str,
        idempotency_key: str,
        principal_id: UUID | None = None,
        *,
        lease_seconds: int = 300,
        worker_id: str | None = None,
    ) -> AgentRunInfo:
        if not idempotency_key.strip():
            raise ValueError("agent idempotency_key is required")
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                cur.execute(
                    "SELECT id,status,task_run_id FROM control.agent_runs WHERE tenant_id=%s AND idempotency_key=%s",
                    (tenant_id, idempotency_key),
                )
                existing = cur.fetchone()
                if existing is not None:
                    return AgentRunInfo(existing[0], existing[2], existing[1])
                cur.execute(
                    "SELECT id,project_id,workflow_run_id,status,attempt,max_attempts,capability,input_payload,lease_expires_at FROM control.task_runs WHERE tenant_id=%s AND id=%s FOR UPDATE",
                    (tenant_id, task_run_id),
                )
                task = cur.fetchone()
                if task is None:
                    raise ValueError("task run not found")
                # A stale lease (worker died mid-run) is reclaimable after expiry.
                stale = task[3] == "running" and task[8] is not None and task[8] < datetime.now(timezone.utc)
                if task[3] not in {"ready", "pending"} and not stale:
                    raise ValueError(f"task is not claimable: {task[3]}")
                # Crash recovery is not a retry: a stale reclaim must not consume
                # the node's attempt budget.
                if task[4] >= task[5] and not stale:
                    raise ValueError("task retry budget exhausted")
                cur.execute(
                    """SELECT 1 FROM control.task_dependencies dep JOIN control.task_runs prerequisite
                    ON prerequisite.id=dep.depends_on_task_run_id
                    WHERE dep.tenant_id=%s AND dep.task_run_id=%s AND prerequisite.status <> 'completed' LIMIT 1""",
                    (tenant_id, task_run_id),
                )
                if cur.fetchone() is not None:
                    raise ValueError("task dependencies are not completed")
                if principal_id is not None:
                    cur.execute("SELECT 1 FROM iam.principals WHERE tenant_id=%s AND id=%s AND status='active'", (tenant_id, principal_id))
                    if cur.fetchone() is None:
                        raise PermissionError("principal is not active in tenant")
                agent_id = uuid4()
                lease_token = uuid4()
                if stale:
                    # Reclaiming a dead worker's claim: close its orphaned agent run
                    # and keep the attempt count (crash recovery is not a retry).
                    cur.execute(
                        "UPDATE control.agent_runs SET status='cancelled',error_detail='lease_expired',finished_at=now() "
                        "WHERE tenant_id=%s AND task_run_id=%s AND status='running'",
                        (tenant_id, task_run_id),
                    )
                    cur.execute(
                        """UPDATE control.task_runs
                        SET status='running',started_at=COALESCE(started_at,now()),
                            lease_token=%s,lease_expires_at=now() + (%s * interval '1 second'),worker_id=%s
                        WHERE tenant_id=%s AND id=%s""",
                        (lease_token, lease_seconds, worker_id, tenant_id, task_run_id),
                    )
                else:
                    cur.execute(
                        """UPDATE control.task_runs
                        SET status='running',attempt=attempt+1,started_at=COALESCE(started_at,now()),
                            lease_token=%s,lease_expires_at=now() + (%s * interval '1 second'),worker_id=%s
                        WHERE tenant_id=%s AND id=%s""",
                        (lease_token, lease_seconds, worker_id, tenant_id, task_run_id),
                    )
                # First claim advances the enclosing run/mission out of the waiting states.
                cur.execute(
                    "UPDATE control.workflow_runs SET status='running',started_at=COALESCE(started_at,now()) "
                    "WHERE tenant_id=%s AND id=%s AND status='pending'",
                    (tenant_id, task[2]),
                )
                cur.execute(
                    """UPDATE control.missions SET status='running'
                    WHERE tenant_id=%s AND id=(SELECT mission_id FROM control.workflow_runs WHERE tenant_id=%s AND id=%s)
                      AND status='planned'""",
                    (tenant_id, tenant_id, task[2]),
                )
                cur.execute(
                    """INSERT INTO control.agent_runs
                    (id,tenant_id,project_id,task_run_id,principal_id,role_key,model_key,status,idempotency_key,started_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,'running',%s,now())""",
                    (agent_id, tenant_id, task[1], task_run_id, principal_id, role_key, model_key, idempotency_key),
                )
                return AgentRunInfo(agent_id, task_run_id, "running")

    def renew_lease(self, agent_run_id: UUID, *, lease_seconds: int = 300, worker_id: str | None = None) -> datetime:
        """Extend a running agent's task lease so long-running executions stay owned.

        Long-running plugins call this periodically (heartbeat); without it the
        lease expires and another worker may reclaim the task mid-execution.
        """
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                cur.execute(
                    "SELECT task_run_id,status FROM control.agent_runs WHERE tenant_id=%s AND id=%s",
                    (tenant_id, agent_run_id),
                )
                agent = cur.fetchone()
                if agent is None:
                    raise ValueError("agent run not found")
                if agent[1] != "running":
                    raise ValueError(f"agent is not running: {agent[1]}")
                cur.execute(
                    "SELECT worker_id,status FROM control.task_runs WHERE tenant_id=%s AND id=%s",
                    (tenant_id, agent[0]),
                )
                task = cur.fetchone()
                if task is None or task[1] != "running":
                    raise ValueError("task run is not running")
                if worker_id is not None and task[0] is not None and task[0] != worker_id:
                    raise PermissionError("task is owned by another worker")
                cur.execute(
                    """UPDATE control.task_runs SET lease_expires_at=now() + (%s * interval '1 second')
                    WHERE tenant_id=%s AND id=%s AND status='running' RETURNING lease_expires_at""",
                    (lease_seconds, tenant_id, agent[0]),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError("task run is not running")
                return cast(datetime, row[0])

    def recover_stale_leases(self, *, worker_grace_seconds: int = 90) -> dict[str, int]:
        """Proactively recover tasks whose lease expired while their worker died.

        A task is only stolen when BOTH its lease is expired AND the claiming
        worker's heartbeat is missing or older than the grace window — a live
        worker that merely runs long is never touched.  Orphaned agent runs are
        closed with ``error_detail='lease_expired_worker_lost'``; tasks with
        retry budget return to ``ready`` (crash recovery is not a retry), tasks
        at max attempts fail and cascade their dependents.
        """
        recovered = 0
        exhausted = 0
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                cur.execute(
                    """SELECT t.id, t.attempt, t.max_attempts, a.id, t.worker_id
                    FROM control.task_runs t
                    JOIN control.agent_runs a ON a.task_run_id=t.id AND a.tenant_id=t.tenant_id
                    WHERE t.tenant_id=%s AND t.status='running' AND t.lease_expires_at IS NOT NULL
                      AND t.lease_expires_at < now() AND a.status='running'""",
                    (tenant_id,),
                )
                for task_id, attempt, max_attempts, agent_id, worker_id in cur.fetchall():
                    if worker_id:
                        cur.execute(
                            "SELECT last_seen FROM ops.worker_heartbeats WHERE worker_id=%s",
                            (worker_id,),
                        )
                        heartbeat = cur.fetchone()
                        if heartbeat is not None and heartbeat[0] is not None:
                            if heartbeat[0] >= datetime.now(timezone.utc) - timedelta(seconds=worker_grace_seconds):
                                continue  # worker is alive; leave the claim alone
                    cur.execute(
                        """UPDATE control.agent_runs
                        SET status='cancelled',error_detail='lease_expired_worker_lost',finished_at=now()
                        WHERE tenant_id=%s AND id=%s AND status='running'""",
                        (tenant_id, agent_id),
                    )
                    if int(attempt) >= int(max_attempts):
                        cur.execute(
                            """UPDATE control.task_runs
                            SET status='failed',error_detail='lease_expired_budget_exhausted',finished_at=now()
                            WHERE tenant_id=%s AND id=%s""",
                            (tenant_id, task_id),
                        )
                        self._cascade_failure(cur, tenant_id, UUID(str(task_id)))
                        exhausted += 1
                    else:
                        cur.execute(
                            """UPDATE control.task_runs
                            SET status='ready',lease_token=NULL,lease_expires_at=NULL,worker_id=NULL
                            WHERE tenant_id=%s AND id=%s""",
                            (tenant_id, task_id),
                        )
                        recovered += 1
        return {"recovered": recovered, "exhausted": exhausted}

    def _complete(self, agent_run_id: UUID, status: str, output: Mapping[str, Any] | None, error: str | None) -> UUID:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid agent terminal status")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                cur.execute("SELECT task_run_id,status FROM control.agent_runs WHERE tenant_id=%s AND id=%s FOR UPDATE", (tenant_id, agent_run_id))
                agent = cur.fetchone()
                if agent is None:
                    raise ValueError("agent run not found")
                if agent[1] != "running":
                    raise ValueError(f"agent run is not running: {agent[1]}")
                task_status = "completed" if status == "completed" else "failed"
                cur.execute(
                    "UPDATE control.agent_runs SET status=%s,output_payload=%s,error_detail=%s,finished_at=now() WHERE tenant_id=%s AND id=%s",
                    (status, Json(dict(output or {})), error, tenant_id, agent_run_id),
                )
                cur.execute(
                    "UPDATE control.task_runs SET status=%s,output_payload=%s,error_detail=%s,finished_at=now() WHERE tenant_id=%s AND id=%s",
                    (task_status, Json(dict(output or {})), error, tenant_id, agent[0]),
                )
                if task_status == "completed":
                    cur.execute(
                        """UPDATE control.task_runs task SET status='ready',scheduled_at=now()
                        WHERE task.tenant_id=%s AND task.status='pending'
                          AND EXISTS (SELECT 1 FROM control.task_dependencies dep WHERE dep.task_run_id=task.id AND dep.depends_on_task_run_id=%s)
                          AND NOT EXISTS (SELECT 1 FROM control.task_dependencies dep JOIN control.task_runs p ON p.id=dep.depends_on_task_run_id
                                          WHERE dep.task_run_id=task.id AND p.status <> 'completed')
                        RETURNING id,capability""",
                        (tenant_id, agent[0]),
                    )
                    for task_id, capability in cur.fetchall():
                        self._enqueue_task_ready(cur, tenant_id, UUID(str(task_id)), str(capability))
                else:
                    # A failed node cancels every dependent whose prerequisites are
                    # already terminal; the workflow can then reach a terminal state.
                    self._cascade_failure(cur, tenant_id, agent[0])
                self._finalize_workflow_run(cur, tenant_id, self._workflow_run_id(cur, tenant_id, agent[0]))
                return UUID(str(agent[0]))

    @staticmethod
    def _workflow_run_id(cur: Any, tenant_id: UUID, task_run_id: UUID) -> UUID | None:
        cur.execute(
            "SELECT workflow_run_id FROM control.task_runs WHERE tenant_id=%s AND id=%s",
            (tenant_id, task_run_id),
        )
        row = cur.fetchone()
        return UUID(str(row[0])) if row is not None else None

    @classmethod
    def _cascade_failure(cls, cur: Any, tenant_id: UUID, failed_task_run_id: UUID) -> None:
        """Mark dependents cancelled once their prerequisites are all terminal.

        Repeats until a fixpoint so a deep DAG collapses in one transaction.
        A dependent is cancelled only when every prerequisite is terminal and
        at least one prerequisite failed or was cancelled.
        """
        while True:
            cur.execute(
                """UPDATE control.task_runs task SET status='cancelled',error_detail='dependency_failed',finished_at=now()
                WHERE task.tenant_id=%s
                  AND task.status='pending'
                  AND NOT EXISTS (
                    SELECT 1 FROM control.task_dependencies dep
                    JOIN control.task_runs prerequisite ON prerequisite.id=dep.depends_on_task_run_id
                    WHERE dep.tenant_id=%s AND dep.task_run_id=task.id
                      AND prerequisite.status NOT IN ('completed','failed','cancelled')
                  )
                  AND EXISTS (
                    SELECT 1 FROM control.task_dependencies dep
                    JOIN control.task_runs prerequisite ON prerequisite.id=dep.depends_on_task_run_id
                    WHERE dep.tenant_id=%s AND dep.task_run_id=task.id
                      AND prerequisite.status IN ('failed','cancelled')
                  )""",
                (tenant_id, tenant_id, tenant_id),
            )
            if cur.rowcount == 0:
                return

    @classmethod
    def _finalize_workflow_run(cls, cur: Any, tenant_id: UUID, workflow_run_id: UUID | None) -> None:
        """Advance a workflow run to completed/failed once every task is terminal."""
        if workflow_run_id is None:
            return
        cur.execute(
            """SELECT count(*),
                      count(*) FILTER (WHERE status='completed'),
                      count(*) FILTER (WHERE status IN ('failed','cancelled'))
               FROM control.task_runs WHERE tenant_id=%s AND workflow_run_id=%s""",
            (tenant_id, workflow_run_id),
        )
        total, completed, failed = cur.fetchone()
        if total is None or int(total) == 0 or int(total) != int(completed) + int(failed):
            return
        terminal = "failed" if int(failed) > 0 else "completed"
        cur.execute(
            "UPDATE control.workflow_runs SET status=%s,finished_at=now() WHERE tenant_id=%s AND id=%s",
            (terminal, tenant_id, workflow_run_id),
        )
        if cur.rowcount == 0:
            return
        cur.execute(
            "SELECT mission_id FROM control.workflow_runs WHERE tenant_id=%s AND id=%s",
            (tenant_id, workflow_run_id),
        )
        mission_row = cur.fetchone()
        if mission_row is not None and mission_row[0] is not None:
            cls._finalize_mission(cur, tenant_id, UUID(str(mission_row[0])))

    @classmethod
    def _finalize_mission(cls, cur: Any, tenant_id: UUID, mission_id: UUID) -> None:
        """Advance a mission to completed/failed once every workflow run is terminal."""
        cur.execute(
            """SELECT count(*),
                      count(*) FILTER (WHERE status IN ('completed','failed','cancelled')),
                      count(*) FILTER (WHERE status IN ('failed','cancelled'))
               FROM control.workflow_runs WHERE tenant_id=%s AND mission_id=%s""",
            (tenant_id, mission_id),
        )
        total, terminal, failed = cur.fetchone()
        if total is None or int(total) == 0 or int(total) != int(terminal):
            return
        cur.execute(
            "UPDATE control.missions SET status=%s WHERE tenant_id=%s AND id=%s",
            ("failed" if int(failed) > 0 else "completed", tenant_id, mission_id),
        )

    def finalize_workflow_run(self, workflow_run_id: UUID) -> str | None:
        """Public finalization entry; returns the terminal status or None."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                self._finalize_workflow_run(cur, tenant_id, workflow_run_id)
                cur.execute(
                    "SELECT status FROM control.workflow_runs WHERE tenant_id=%s AND id=%s",
                    (tenant_id, workflow_run_id),
                )
                row = cur.fetchone()
                return str(row[0]) if row is not None else None

    def reconcile_workflow_states(self) -> dict[str, int]:
        """Backfill terminal states for legacy runs whose tasks are all terminal.

        Fixes the inconsistent demo projection (tasks completed under pending
        runs) without touching finished evidence.  Returns the counts of runs
        and missions advanced.
        """
        runs_advanced = 0
        missions_advanced = 0
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur, self.tenant_slug)
                cur.execute(
                    """SELECT id FROM control.workflow_runs
                    WHERE tenant_id=%s AND status NOT IN ('completed','failed','cancelled')""",
                    (tenant_id,),
                )
                run_ids = [UUID(str(row[0])) for row in cur.fetchall()]
                for run_id in run_ids:
                    self._finalize_workflow_run(cur, tenant_id, run_id)
                    cur.execute(
                        "SELECT status FROM control.workflow_runs WHERE tenant_id=%s AND id=%s",
                        (tenant_id, run_id),
                    )
                    row = cur.fetchone()
                    if row is not None and str(row[0]) in {"completed", "failed", "cancelled"}:
                        runs_advanced += 1
                cur.execute(
                    """SELECT id FROM control.missions
                    WHERE tenant_id=%s AND status NOT IN ('completed','failed','cancelled')""",
                    (tenant_id,),
                )
                for mission_row in cur.fetchall():
                    mission_id = UUID(str(mission_row[0]))
                    before_status: str | None = None
                    cur.execute(
                        "SELECT status FROM control.missions WHERE tenant_id=%s AND id=%s",
                        (tenant_id, mission_id),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        before_status = str(row[0])
                    self._finalize_mission(cur, tenant_id, mission_id)
                    cur.execute(
                        "SELECT status FROM control.missions WHERE tenant_id=%s AND id=%s",
                        (tenant_id, mission_id),
                    )
                    row = cur.fetchone()
                    if row is not None and str(row[0]) != before_status:
                        missions_advanced += 1
        return {"runs_advanced": runs_advanced, "missions_advanced": missions_advanced}

    def complete_agent(self, agent_run_id: UUID, output: Mapping[str, Any] | None = None) -> UUID:
        return self._complete(agent_run_id, "completed", output, None)

    def fail_agent(self, agent_run_id: UUID, error: str) -> UUID:
        return self._complete(agent_run_id, "failed", {}, error)


class PluginExecutor:
    """In-process allow-listed plugin handler registry."""

    def __init__(self, scheduler: Scheduler) -> None:
        self.scheduler = scheduler
        self._handlers: dict[str, PluginHandler] = {}

    def register(self, capability: str, handler: PluginHandler) -> None:
        if not capability.strip() or capability in self._handlers:
            raise ValueError("capability must be unique and non-empty")
        self._handlers[capability] = handler

    def execute(self, agent_run_id: UUID) -> dict[str, Any]:
        with psycopg2.connect(self.scheduler.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self.scheduler._tenant(cur, self.scheduler.tenant_slug)
                cur.execute(
                    """SELECT a.task_run_id,t.capability,t.input_payload,t.trace_id FROM control.agent_runs a
                    JOIN control.task_runs t ON t.id=a.task_run_id AND t.tenant_id=a.tenant_id
                    WHERE a.tenant_id=%s AND a.id=%s AND a.status='running'""",
                    (tenant_id, agent_run_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError("running agent run not found")
                capability, payload, trace_id = str(row[1]), dict(row[2] or {}), row[3]
                cur.execute(
                    "SELECT rules FROM policy.policy_sets "
                    "WHERE tenant_id=%s AND status='active' ORDER BY created_at ASC, id ASC",
                    (tenant_id,),
                )
                rules = [
                    rule
                    for policy_row in cur.fetchall()
                    if isinstance(policy_row[0], list)
                    for rule in policy_row[0]
                    if isinstance(rule, dict)
                ]
                decision = PolicyEngine(rules=rules).evaluate(capability, payload, "medium")
                # Persist the same immutable audit evidence the API records.
                from packages.policy.gateway import record_decision

                record_decision(
                    cur, tenant_id, capability, payload, decision,
                    UUID(str(trace_id)) if trace_id else uuid4(),
                )
        handler = self._handlers.get(capability)
        if decision.decision != "allow":
            self.scheduler.fail_agent(agent_run_id, "policy_denied")
            raise PermissionError(f"policy denied plugin capability: {capability}")
        if handler is None:
            self.scheduler.fail_agent(agent_run_id, "plugin_not_registered")
            raise ValueError(f"plugin capability not registered: {capability}")
        try:
            result = handler(payload)
            if not isinstance(result, dict):
                raise TypeError("plugin handler must return a dict")
        except Exception as exc:
            self.scheduler.fail_agent(agent_run_id, str(exc))
            raise
        self.scheduler.complete_agent(agent_run_id, result)
        return result
