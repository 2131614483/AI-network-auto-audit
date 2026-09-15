"""24x7 task executor: claims and runs one ready task per call.

The executor is deliberately serial (one task per call) like the rich-media
queue, so a single worker can never contend with itself.  Every execution is
policy-gated and the decision is persisted through ``packages.policy.gateway``
before the handler runs; a crash after claim is recovered by lease expiry
(``Scheduler.claim_task`` reclaims stale ``running`` tasks and cancels the
orphaned agent run).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from packages.control.scheduler import PluginExecutor, Scheduler
from packages.observability import trace_context

PluginHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _echo(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "input": payload.get("input")}


def _verify(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "verified_input": payload.get("input")}


def _report(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "reported_input": payload.get("input")}


# Deterministic in-process handlers for the demo DAG; no shell, no network.
DEFAULT_DEMO_HANDLERS: dict[str, PluginHandler] = {
    "scheduler.a": _echo,
    "scheduler.b": _verify,
    "scheduler.c": _report,
}


@dataclass(frozen=True, slots=True)
class DagRunResult:
    task_count: int
    agent_count: int


def run_mission_dag(
    scheduler: Scheduler,
    executor: PluginExecutor,
    workflow_run_id: Any,
    *,
    stop_at_terminal: bool = True,
) -> DagRunResult:
    """Drive a workflow run to completion through the real claim/execute path."""
    task_count = 0
    agent_count = 0
    while True:
        ready = scheduler.ready_tasks(limit=1, workflow_run_id=workflow_run_id)
        if not ready:
            break
        task = ready[0]
        claim = scheduler.claim_task(
            task.id,
            role_key="scheduler-worker",
            model_key="local-demo",
            idempotency_key=f"agent-{uuid4().hex}",
            lease_seconds=300,
            worker_id="seed-demo",
        )
        with trace_context(str(task.trace_id)):
            executor.execute(claim.id)
        task_count += 1
        agent_count += 1
    return DagRunResult(task_count, agent_count)


def process_ready_task_once(
    database_url: str,
    *,
    tenant_slug: str = "local-dev",
    handlers: dict[str, PluginHandler] | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 300,
    workflow_run_id: Any = None,
) -> bool:
    """Claim and execute at most one ready task; returns False when idle.

    The worker passes no ``workflow_run_id`` (tenant-wide).  Tests and seeds
    pass a run id to keep their claims scoped.
    """
    scheduler = Scheduler(database_url, tenant_slug)
    executor = PluginExecutor(scheduler)
    for capability, handler in (handlers or DEFAULT_DEMO_HANDLERS).items():
        executor.register(capability, handler)
    tasks = scheduler.ready_tasks(limit=1, workflow_run_id=workflow_run_id)
    if not tasks:
        return False
    task = tasks[0]
    claim = scheduler.claim_task(
        task.id,
        role_key="scheduler-worker",
        model_key="local-demo",
        idempotency_key=f"agent-{uuid4().hex}",
        lease_seconds=lease_seconds,
        worker_id=worker_id,
    )
    with trace_context(str(task.trace_id)):
        executor.execute(claim.id)
    return True
