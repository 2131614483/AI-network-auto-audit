"""audit.mandate.project-library: register scored proposals into the project
library snapshot.

Consumes a scored-proposal series (metric-series) and emits a
dataset-validation project snapshot whose checks carry one entry per
project (message-encoded, schema-compliant). Read-only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.mandate.project-library"
CAPABILITY = "audit.mandate.project-library"
MAX_PROJECTS = 10_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("scored-proposal")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("scored-proposal reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    points = payload.get("points")
    if not isinstance(points, list) or not points:
        raise InputRejected("scored-proposal requires points array")

    checks = []
    for index, point in enumerate(points[:MAX_PROJECTS], start=1):
        if not isinstance(point, dict):
            continue
        try:
            score = float(point.get("value") or 0)
        except (TypeError, ValueError):
            continue
        checks.append({
            "check_id": "columns",
            "status": "pass",
            "message": f"project:PR-{index:04d} score:{score:g} admitted:true",
        })
    if not checks:
        raise InputRejected("scored-proposal contains no scored projects")

    snapshot_sha = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": snapshot_sha,
        "summary": {"valid": True, "checked_columns": 3, "checked_rows": len(checks),
                    "missing_values": 0},
        "checks": checks,
        "violations": [],
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
