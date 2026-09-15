# -*- coding: utf-8 -*-
"""audit.remedy.remedy-plan-review: review the remedy plan: approve when every task has an owner and due date

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

PLUGIN_ID = "audit.remedy.remedy-plan-review"
CAPABILITY = "audit.remedy.remedy-plan-review"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("remedy-task")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("remedy-task reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    workflow = read_artifact_json(artifact, roots)
    if not isinstance(workflow, dict):
        raise InputRejected("remedy-task payload must be an object")

    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise InputRejected("remedy-task nodes must be a non-empty array")

    missing: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if not node.get("owner"):
            missing.append(str(node.get("task_id") or "?"))
    approved = not missing
    return {
        "contract_id": "workflow", "contract_version": "1.0.0",
        "workflow_id": workflow.get("workflow_id", "remedy-2026"),
        "kind": "remedy-plan-review",
        "nodes": nodes,
        "summary": {
            "approved": approved,
            "task_count": len(nodes),
            "missing_owner": missing,
            "note": "整改方案审核通过" if approved else "存在未指定责任人的任务，退回补齐",
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
