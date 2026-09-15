"""audit.field.audit-log: normalise raw operation events into an ordered
audit-log workflow.

Consumes a log-event payload (artifact-ref JSON) and emits a workflow whose
nodes are the events in chronological order and whose edges chain them by
time. Field values are passed through as-is. Read-only.
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

PLUGIN_ID = "audit.field.audit-log"
CAPABILITY = "audit.field.audit-log"
MAX_EVENTS = 50_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("log-event")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("log-event reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    events = payload.get("events")
    if not isinstance(events, list):
        raise InputRejected("log-event requires events array")

    ordered = [e for e in events[:MAX_EVENTS] if isinstance(e, dict)]
    ordered.sort(key=lambda e: str(e.get("ts") or ""))
    if not ordered:
        raise InputRejected("log-event contains no events")

    nodes = []
    edges = []
    for index, event in enumerate(ordered, start=1):
        node_id = f"log-{index:04d}"
        nodes.append({
            "id": node_id,
            "name": str(event.get("op") or "operation"),
            "props": {
                "node": str(event.get("node") or ""),
                "ts": str(event.get("ts") or ""),
                "detail": str(event.get("detail") or ""),
                "actor": str(event.get("actor") or ""),
            },
        })
        if index > 1:
            edges.append({"id": f"e-{index:04d}", "source": f"log-{index - 1:04d}", "target": node_id})

    return {
        "workflow_id": f"audit-log-{payload.get('log_id') or 'session'}",
        "tenant_id": str(payload.get("tenant_id") or "local-dev"),
        "key": "audit-log", "version": "1.0.0",
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        "nodes": nodes, "edges": edges,
        "checksum": {"event_count": len(ordered)},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
