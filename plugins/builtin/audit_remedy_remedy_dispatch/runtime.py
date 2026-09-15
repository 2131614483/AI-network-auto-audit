# -*- coding: utf-8 -*-
"""audit.remedy.remedy-dispatch: dispatch the final issue set into remedy tasks

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

PLUGIN_ID = "audit.remedy.remedy-dispatch"
CAPABILITY = "audit.remedy.remedy-dispatch"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("final-issue-set")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("final-issue-set reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    findings = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(findings, list) or not findings:
        raise InputRejected("final-issue-set requires findings array")

    tasks = []
    for index, finding in enumerate(findings[:5_000]):
        if not isinstance(finding, dict):
            continue
        issue_id = str(finding.get("issue_id") or f"ISSUE-{index + 1:04d}")
        severity = str(finding.get("severity") or "low")
        due_days = {"high": 30, "medium": 60, "low": 90}.get(severity, 90)
        tasks.append({
            "task_id": f"RT-{index + 1:04d}",
            "name": f"整改-{issue_id}",
            "issue_ref": issue_id,
            "severity": severity,
            "owner": "",
            "due_date": "",
            "status": "not_started",
            "due_days": due_days,
        })
    if not tasks:
        raise InputRejected("final-issue-set contains no dispatchable issues")

    return {
        "contract_id": "workflow", "contract_version": "1.0.0",
        "workflow_id": "remedy-dispatch-2026",
        "kind": "remedy-dispatch",
        "nodes": tasks,
        "summary": {"task_count": len(tasks), "high": sum(1 for t in tasks if t["severity"] == "high")},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
