"""audit.field.cross-dept-inquiry: turn data-inquiry requests into a
request->decision->delivery workflow.

Consumes an inquiry payload (artifact-ref JSON) and emits a workflow with one
node-chain per request. Each request's stated status is passed through;
missing statuses are marked pending (never invented). Read-only.
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

PLUGIN_ID = "audit.field.cross-dept-inquiry"
CAPABILITY = "audit.field.cross-dept-inquiry"
MAX_REQUESTS = 20_000

_STATUSES = ("requested", "approved", "delivered", "rejected")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("inquiry-request")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("inquiry-request reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    requests = payload.get("requests")
    if not isinstance(requests, list):
        raise InputRejected("inquiry-request requires requests array")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for index, request in enumerate(requests[:MAX_REQUESTS], start=1):
        if not isinstance(request, dict):
            continue
        status = str(request.get("status") or "pending")
        if status not in _STATUSES and status != "pending":
            status = "pending"
        chain_id = f"inq-{index:04d}"
        nodes.append({
            "id": f"{chain_id}-req",
            "name": "协查请求",
            "props": {"dept": str(request.get("dept") or ""),
                      "data_type": str(request.get("data_type") or ""),
                      "purpose": str(request.get("purpose") or ""),
                      "status": status},
        })
        nodes.append({
            "id": f"{chain_id}-flow",
            "name": "流转节点",
            "props": {"next": status, "requested_by": str(request.get("requested_by") or "")},
        })
        edges.append({"id": f"{chain_id}-e1", "source": f"{chain_id}-req", "target": f"{chain_id}-flow"})
    if not nodes:
        raise InputRejected("inquiry-request contains no requests")

    return {
        "workflow_id": f"inquiry-{payload.get('period') or 'period'}",
        "tenant_id": str(payload.get("tenant_id") or "local-dev"),
        "key": "cross-dept-inquiry", "version": "1.0.0",
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        "nodes": nodes, "edges": edges,
        "checksum": {"request_count": len(nodes) // 2,
                     "by_status": {s: sum(1 for n in nodes if n["props"].get("status") == s)
                                   for s in (*_STATUSES, "pending")}},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
