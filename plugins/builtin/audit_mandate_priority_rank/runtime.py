"""audit.mandate.priority-rank: rank projects by their admission score.

Consumes a project snapshot (dataset-validation) whose checks carry
message-encoded project entries and emits a priority-order series sorted by
score descending. Read-only.
"""
from __future__ import annotations

import re
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.mandate.priority-rank"
CAPABILITY = "audit.mandate.priority-rank"
_ENTRY = re.compile(r"project:(\S+) score:([0-9.]+)")


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
    if not isinstance(checks, list):
        raise InputRejected("project-snapshot requires checks array")

    ranked = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        match = _ENTRY.search(str(check.get("message") or ""))
        if not match:
            continue
        ranked.append((str(match.group(1)), float(match.group(2))))
    if not ranked:
        raise InputRejected("project-snapshot contains no scored projects")
    ranked.sort(key=lambda item: (-item[1], item[0]))

    points = [
        {"at": f"2026-01-{index:02d}T00:00:00+08:00", "value": score}
        for index, (_, score) in enumerate(ranked, start=1)
    ]
    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": "priority-order-2026",
        "metric": "priority_order", "unit": "rank", "window_minutes": 1440,
        "points": points,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
