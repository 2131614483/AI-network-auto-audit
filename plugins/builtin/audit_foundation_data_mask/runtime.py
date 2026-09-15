# -*- coding: utf-8 -*-
"""audit.foundation.data-mask: simulated masking of sensitive fields

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

PLUGIN_ID = "audit.foundation.data-mask"
CAPABILITY = "audit.foundation.data-mask"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("mask-request")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("mask-request reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise InputRejected("mask-request requires rows array")
    masked = []
    for row in rows:
        if isinstance(row, dict):
            new = dict(row)
            for key in list(new):
                if any(t in key.lower() for t in ("name", "id", "phone", "account", "card")):
                    new[key] = "***MASKED***"
            masked.append(new)
    return {
        "contract_id": "artifact-ref", "contract_version": "1.0.0",
        "artifact": {
            "uri": artifact.get("uri", ""), "sha256": artifact.get("sha256", "0" * 64),
            "size_bytes": artifact.get("size_bytes", 0),
            "masked": True, "masked_rows": len(masked),
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
