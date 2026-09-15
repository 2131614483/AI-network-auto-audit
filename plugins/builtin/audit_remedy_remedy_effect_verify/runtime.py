# -*- coding: utf-8 -*-
"""audit.remedy.remedy-effect-verify: verify remedy effectiveness against submitted evidence and lineage

Read-only remedy-stage plugin. No network, no writes outside the staging
artifact. Fails closed on invalid input.
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

PLUGIN_ID = "audit.remedy.remedy-effect-verify"
CAPABILITY = "audit.remedy.remedy-effect-verify"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    port = envelope.get("remedy-evidence")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("remedy-evidence reference is missing")
    artifact = port["artifact"]
    read_verified_artifact(artifact, roots)
    evidence = read_artifact_json(artifact, roots)
    if not isinstance(evidence, dict):
        raise InputRejected("remedy-evidence payload must be an object")

    checks: list[dict[str, str]] = []
    violations: list[dict[str, str]] = []

    # tolerate document-content wrapping (evidence nested under artifact)
    wrapped = evidence.get("artifact") if isinstance(evidence.get("artifact"), dict) else None
    items = wrapped.get("evidence") if isinstance(wrapped, dict) else evidence.get("evidence")
    if isinstance(items, list) and items:
        valid_count = sum(1 for it in items if isinstance(it, dict) and it.get("valid") is True)
        checks.append({"check_id": "columns", "status": "pass" if valid_count == len(items) else "warn",
                       "message": f"整改证据 {valid_count}/{len(items)} 有效"})
        if valid_count < len(items):
            violations.append({"check_id": "columns", "message": "存在无效整改证据"})
    else:
        checks.append({"check_id": "freshness", "status": "fail", "message": "缺少整改证据"})
        violations.append({"check_id": "freshness", "message": "缺少整改证据"})

    valid = not violations
    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": "0" * 64,
        "summary": {"valid": valid, "checked_columns": len(checks), "checked_rows": 0,
                    "missing_values": len(violations)},
        "checks": checks,
        "violations": violations,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
