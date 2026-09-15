# -*- coding: utf-8 -*-
"""audit.foundation.permission-control: simulated role-based access decision

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

PLUGIN_ID = "audit.foundation.permission-control"
CAPABILITY = "audit.foundation.permission-control"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("access-request")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("access-request reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    role = str(payload.get("role") or "")
    action = str(payload.get("action") or "")
    allowed_roles = {"auditor", "audit_manager", "system_admin"}
    allow = role in allowed_roles and bool(action)
    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": "0" * 64,
        "summary": {"valid": allow, "checked_columns": 1, "checked_rows": 1, "missing_values": 0},
        "checks": [{"check_id": "columns", "status": "pass" if allow else "fail", "message": f"权限 {role}:{action}"}],
        "violations": [] if allow else [{"check_id": "columns", "message": "权限不足"}],
        "decision": {"allow": allow, "role": role, "action": action},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
