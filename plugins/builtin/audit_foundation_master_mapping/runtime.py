# -*- coding: utf-8 -*-
"""audit.foundation.master-mapping: map master data codes across systems

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

PLUGIN_ID = "audit.foundation.master-mapping"
CAPABILITY = "audit.foundation.master-mapping"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("master-source")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("master-source reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    mappings = payload.get("mappings") if isinstance(payload, dict) else None
    if not isinstance(mappings, list) or not mappings:
        raise InputRejected("master-source requires mappings")
    ok = [m for m in mappings if isinstance(m, dict) and m.get("source_code") and m.get("target_code")]
    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": "0" * 64,
        "summary": {"valid": bool(ok), "checked_columns": len(ok), "checked_rows": len(ok), "missing_values": 0},
        "checks": [{"check_id": "columns", "status": "pass" if ok else "fail", "message": f"映射 {len(ok)} 条"}],
        "violations": [] if ok else [{"check_id": "columns", "message": "映射缺失"}],
        "mapped": ok,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
