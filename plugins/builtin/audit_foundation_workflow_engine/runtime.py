# -*- coding: utf-8 -*-
"""audit.foundation.workflow-engine: simulated workflow state machine

Read-only/simulated support-layer plugin. No network, no writes outside the
staging artifact. Fails closed on invalid input.
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

PLUGIN_ID = "audit.foundation.workflow-engine"
CAPABILITY = "audit.foundation.workflow-engine"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("workflow-request")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("workflow-request reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    nodes = payload.get("nodes") if isinstance(payload, dict) else None
    if not isinstance(nodes, list) or not nodes:
        raise InputRejected("workflow-request requires nodes")
    done = sum(1 for n in nodes if isinstance(n, dict) and n.get("state") == "approved")
    return {
        "contract_id": "workflow", "contract_version": "1.0.0",
        "workflow_id": "wf-foundation", "nodes": nodes,
        "summary": {"approved": done, "total": len(nodes), "state": "running" if done < len(nodes) else "completed"},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
