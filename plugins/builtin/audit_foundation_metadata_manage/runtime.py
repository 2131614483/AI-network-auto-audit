# -*- coding: utf-8 -*-
"""audit.foundation.metadata-manage: manage metadata dictionary

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

PLUGIN_ID = "audit.foundation.metadata-manage"
CAPABILITY = "audit.foundation.metadata-manage"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("metadata-query")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("metadata-query reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    dictionary = payload.get("dictionary") if isinstance(payload, dict) else None
    if not isinstance(dictionary, list) or not dictionary:
        raise InputRejected("metadata-query requires dictionary")
    cols = [c for c in dictionary if isinstance(c, dict) and c.get("column")]
    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": "0" * 64,
        "summary": {"valid": bool(cols), "checked_columns": len(cols), "checked_rows": len(cols), "missing_values": 0},
        "checks": [{"check_id": "columns", "status": "pass" if cols else "fail", "message": f"字典 {len(cols)} 列"}],
        "violations": [] if cols else [{"check_id": "columns", "message": "字典为空"}],
        "dictionary": cols,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
