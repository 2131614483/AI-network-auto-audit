"""audit.mandate.material-precheck: pre-check submitted materials against
the required list.

Consumes a submitted-material bundle (artifact-ref) and emits a
dataset-validation precheck report; missing required materials become
violations. Read-only.
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

PLUGIN_ID = "audit.mandate.material-precheck"
CAPABILITY = "audit.mandate.material-precheck"
_REQUIRED = ("通知书回执", "营业执照", "财务报表", "内控文档", "资金流水")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("submitted-material")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("submitted-material reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    units = payload.get("metadata", {}).get("units") if isinstance(payload.get("metadata"), dict) else None
    if not isinstance(units, list):
        units = payload.get("units")
    if not isinstance(units, list):
        raise InputRejected("submitted-material requires units array")

    violations = []
    checked_rows = 0
    for unit in units:
        if not isinstance(unit, dict):
            continue
        checked_rows += 1
        materials = unit.get("materials")
        if not isinstance(materials, list):
            materials = []
        present = {str(m) for m in materials}
        for required in _REQUIRED:
            if required not in present:
                violations.append({
                    "check_id": "missing_rate",
                    "message": f"单位 {unit.get('unit_name') or '?'} 缺少必报材料：{required}",
                })

    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "summary": {"valid": not violations, "checked_columns": 5, "checked_rows": checked_rows,
                    "missing_values": len(violations)},
        "checks": [{"check_id": "missing_rate", "status": "fail" if violations else "pass",
                    "message": f"材料缺失 {len(violations)} 项"}],
        "violations": violations,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
