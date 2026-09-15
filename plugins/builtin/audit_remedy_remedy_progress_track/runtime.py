"""audit.remedy.remedy-progress-track: track remedy task execution.

Consumes an approved remedy workflow (nodes carry task props: owner,
due_date, status) and produces a progress projection: completion rate,
overdue detection and a metric-series point.  Read-only tracking.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.remedy.remedy-progress-track"
CAPABILITY = "audit.remedy.remedy-progress-track"
STATUS_DONE = {"done", "completed", "完成", "已完成", "closed", "销号"}
STATUS_PROGRESS = {"in_progress", "progress", "进行中", "处理中"}
STATUS_OVERDUE = {"overdue", "逾期"}


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    port = envelope.get("remedy-plan-approved")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("remedy-plan-approved reference is missing")
    artifact = port["artifact"]
    read_verified_artifact(artifact, roots)
    workflow = read_artifact_json(artifact, roots)
    if not isinstance(workflow, dict):
        raise InputRejected("remedy plan payload must be an object")

    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise InputRejected("remedy plan nodes must be an array")
    as_of = _parse_date(workflow.get("as_of")) if workflow.get("as_of") else date.today()

    tasks: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        props = node.get("props")
        if not isinstance(props, dict):
            continue
        task = {
            "task_id": str(node.get("id") or node.get("node_id") or "?"),
            "name": str(node.get("name") or ""),
            "owner": str(props.get("owner") or ""),
            "due_date": str(props.get("due_date") or ""),
            "status": str(props.get("status") or "not_started"),
        }
        tasks.append(task)

    done = sum(1 for task in tasks if task["status"].lower() in STATUS_DONE)
    progress = sum(1 for task in tasks if task["status"].lower() in STATUS_PROGRESS)
    overdue: list[dict[str, Any]] = []
    for task in tasks:
        status = task["status"].lower()
        due = _parse_date(task["due_date"])
        if status in STATUS_OVERDUE or (due is not None and due < as_of and status not in STATUS_DONE):
            overdue.append({**task, "overdue_days": (as_of - due).days if due else None})

    total = len(tasks)
    completion_rate = round(done / total, 6) if total else 0.0
    return {
        "contract_id": "remedy-progress",
        "contract_version": "1.0.0",
        "workflow_id": workflow.get("workflow_id"),
        "as_of": as_of.isoformat(),
        "summary": {
            "total": total,
            "done": done,
            "in_progress": progress,
            "overdue": len(overdue),
            "completion_rate": completion_rate,
        },
        "overdue_tasks": overdue,
        "series": [
            {
                "contract_id": "metric-series",
                "contract_version": "1.0.0",
                "series_id": "remedy-completion-rate",
                "metric": "整改完成率",
                "unit": "ratio",
                "window_minutes": 10080,
                "points": [{"at": f"{as_of.isoformat()}T00:00:00+00:00", "value": completion_rate}],
            }
        ],
    }


if __name__ == "__main__":
    from plugins.builtin._child_common import child_main

    raise SystemExit(child_main(handle))
