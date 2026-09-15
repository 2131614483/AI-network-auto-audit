"""audit.plan.staff-schedule: build the daily staffing schedule from the
team scheme.

Consumes a team-scheme workflow and emits a schedule-plan workflow assigning
named members (when present) to daily slots; unfilled members stay
pending_human. Read-only.
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

PLUGIN_ID = "audit.plan.staff-schedule"
CAPABILITY = "audit.plan.staff-schedule"
_DAYS = ("D1", "D2", "D3", "D4", "D5")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("team-scheme")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("team-scheme reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise InputRejected("team-scheme requires nodes array")

    roles = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        roles.append({"role": str(node.get("name") or ""),
                      "member": str(props.get("member") or ""),
                      "pending": bool(props.get("pending_human"))})
    if not roles:
        raise InputRejected("team-scheme contains no roles")

    schedule_nodes = []
    for day_index, day in enumerate(_DAYS, start=1):
        schedule_nodes.append({"id": f"day-{day_index}", "name": day, "props": {
            "slots": [{"role": r["role"], "member": r["member"] or None,
                       "status": "pending_human" if r["pending"] else "assigned"}
                      for r in roles]}})
    assigned = sum(1 for r in roles if not r["pending"])
    return {
        "workflow_id": f"schedule-{payload.get('workflow_id') or 'team'}",
        "tenant_id": str(payload.get("tenant_id") or "local-dev"),
        "key": "staff-schedule", "version": "1.0.0",
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        "nodes": schedule_nodes,
        "edges": [{"id": f"e{i}", "source": f"day-{i}", "target": f"day-{i + 1}"}
                  for i in range(1, len(schedule_nodes))],
        "checksum": {"days": len(schedule_nodes), "assigned_members": assigned},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
