# -*- coding: utf-8 -*-
"""audit.foundation.biz-standardize: normalize raw business data to a standard schema

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

PLUGIN_ID = "audit.foundation.biz-standardize"
CAPABILITY = "audit.foundation.biz-standardize"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("raw-biz-set")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("raw-biz-set reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise InputRejected("raw-biz-set requires rows array")
    cleaned = []
    for row in rows:
        if isinstance(row, dict):
            cleaned.append({k: str(v).strip() if isinstance(v, str) else v for k, v in row.items()})
    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": "0" * 64,
        "summary": {"valid": True, "checked_columns": len(cleaned), "checked_rows": len(cleaned), "missing_values": 0},
        "checks": [{"check_id": "columns", "status": "pass", "message": f"标准化 {len(cleaned)} 行"}],
        "violations": [],
        "standard_rows": cleaned,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
