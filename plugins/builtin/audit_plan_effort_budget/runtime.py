"""audit.plan.effort-budget: derive the effort budget from the schedule.

Consumes a schedule-plan workflow and emits an effort-budget metric series:
budget hours = assigned members × days × 8h/day. Unassigned (pending) members
do not consume budget. Read-only.
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

PLUGIN_ID = "audit.plan.effort-budget"
CAPABILITY = "audit.plan.effort-budget"
_HOURS_PER_DAY = 8.0


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("schedule-plan")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("schedule-plan reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise InputRejected("schedule-plan requires nodes array")

    days = 0
    person_days = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        slots = props.get("slots")
        if not isinstance(slots, list):
            continue
        days += 1
        person_days += sum(1 for slot in slots if isinstance(slot, dict) and slot.get("status") == "assigned")
    if days == 0:
        raise InputRejected("schedule-plan contains no schedule days")

    budget = person_days * _HOURS_PER_DAY
    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": f"effort-{payload.get('workflow_id') or 'schedule'}",
        "metric": "effort_budget", "unit": "hours", "window_minutes": 1440,
        "points": [{"at": "2026-01-01T00:00:00+08:00", "value": budget}],
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
