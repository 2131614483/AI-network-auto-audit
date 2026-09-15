"""audit.plan.plan-version-control: version a plan from change requests.

Consumes a plan-change payload (document-content) and emits a plan-version
workflow chaining versions v1..vN with their change descriptions. Read-only.
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

PLUGIN_ID = "audit.plan.plan-version-control"
CAPABILITY = "audit.plan.plan-version-control"
MAX_CHANGES = 10_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("plan-change")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("plan-change reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    body = payload.get("artifact")
    changes = body.get("changes") if isinstance(body, dict) else payload.get("changes")
    if not isinstance(changes, list) or not changes:
        raise InputRejected("plan-change requires changes array")

    plan_key = str(payload.get("plan_key") or (body.get("plan_key") if isinstance(body, dict) else "") or "plan")
    nodes = []
    edges = []
    for index, change in enumerate(changes[:MAX_CHANGES], start=1):
        node_id = f"v{index}"
        nodes.append({"id": node_id, "name": f"版本 v{index}", "props": {
            "plan_key": plan_key, "change": str(change.get("description") or change.get("change") or ""),
            "author": str(change.get("author") or ""), "status": "current" if index == len(changes[:MAX_CHANGES]) else "superseded"}})
        if index > 1:
            edges.append({"id": f"e{index}", "source": f"v{index - 1}", "target": node_id})

    return {
        "workflow_id": f"plan-version-{plan_key}",
        "tenant_id": str(payload.get("tenant_id") or "local-dev"),
        "key": "plan-version-control", "version": "1.0.0",
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        "nodes": nodes, "edges": edges,
        "checksum": {"version_count": len(nodes), "current": f"v{len(nodes)}"},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
