# -*- coding: utf-8 -*-
"""audit.remedy.remedy-overdue-alert: alert on overdue remedy tasks from the progress projection

Read-only remedy-stage plugin. No network, no writes outside the staging
artifact. Fails closed on invalid input.
"""
from __future__ import annotations

from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.remedy.remedy-overdue-alert"
CAPABILITY = "audit.remedy.remedy-overdue-alert"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("remedy-progress")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("remedy-progress reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("remedy-progress payload must be an object")

    summary = payload.get("summary")
    overdue_tasks = payload.get("overdue_tasks")
    if not isinstance(summary, dict) or not isinstance(overdue_tasks, list):
        raise InputRejected("remedy-progress requires summary and overdue_tasks")

    alerts = []
    for task in overdue_tasks[:1_000]:
        if not isinstance(task, dict):
            continue
        alerts.append({
            "task_id": str(task.get("task_id") or "?"),
            "name": str(task.get("name") or ""),
            "owner": str(task.get("owner") or ""),
            "overdue_days": task.get("overdue_days"),
            "message": "整改任务已逾期，请督办",
        })
    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": "remedy-overdue-alert",
        "metric": "整改逾期预警",
        "unit": "count",
        "window_minutes": 10080,
        "points": [{"at": "2026-09-10T00:00:00+00:00", "value": len(alerts)}],
        "alerts": alerts,
        "summary": {"overdue_count": len(alerts), "completion_rate": summary.get("completion_rate")},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
