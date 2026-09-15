"""audit.mandate.team-forming: form the audit team skeleton for a project.

Consumes a project snapshot (dataset-validation) and emits a team-scheme
workflow with the standard role skeleton (组长/主审/助审/复核). Members are
left pending_human; nobody is invented. Read-only.
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

PLUGIN_ID = "audit.mandate.team-forming"
CAPABILITY = "audit.mandate.team-forming"
_ROLES = ("组长", "主审", "助审", "复核")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("project-snapshot")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("project-snapshot reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    checks = payload.get("checks")
    if not isinstance(checks, list) or not checks:
        raise InputRejected("project-snapshot requires checks array")
    project = str(checks[0].get("message") or "project:UNKNOWN").split(" ")[0].replace("project:", "")

    nodes = []
    edges = []
    for index, role in enumerate(_ROLES, start=1):
        node_id = f"role-{index}"
        nodes.append({"id": node_id, "name": role, "props": {
            "project": project, "member": "", "pending_human": True,
            "responsibility": {"组长": "统筹", "主审": "程序执行", "助审": "底稿协助", "复核": "质量复核"}[role]}})
        if index > 1:
            edges.append({"id": f"e-{index}", "source": f"role-{index - 1}", "target": node_id})

    return {
        "workflow_id": f"team-{project}",
        "tenant_id": str(payload.get("tenant_id") or "local-dev"),
        "key": "team-forming", "version": "1.0.0",
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        "nodes": nodes, "edges": edges,
        "checksum": {"role_count": len(nodes), "pending_human": len(nodes)},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
