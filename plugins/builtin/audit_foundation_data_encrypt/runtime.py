# -*- coding: utf-8 -*-
"""audit.foundation.data-encrypt: simulated encrypt-ref of sensitive artifacts

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

PLUGIN_ID = "audit.foundation.data-encrypt"
CAPABILITY = "audit.foundation.data-encrypt"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("encrypt-request")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("encrypt-request reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    read_artifact_json(artifact, roots)
    src = artifact.get("uri", "")
    return {
        "contract_id": "artifact-ref", "contract_version": "1.0.0",
        "artifact": {
            "uri": src, "sha256": artifact.get("sha256", "0" * 64),
            "size_bytes": artifact.get("size_bytes", 0),
            "encrypted": True, "cipher": "simulated-aes256",
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
