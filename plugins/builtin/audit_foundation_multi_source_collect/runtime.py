# -*- coding: utf-8 -*-
"""audit.foundation.multi-source-collect: simulated multi-system audit data collection

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

PLUGIN_ID = "audit.foundation.multi-source-collect"
CAPABILITY = "audit.foundation.multi-source-collect"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("audit-source-request")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("audit-source-request reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, list) or not sources:
        raise InputRejected("audit-source-request requires sources")
    collected = [
        {"source": str(s), "status": "collected", "rows": 1000}
        for s in sources if isinstance(s, str) and s
    ]
    if not collected:
        raise InputRejected("no valid sources to collect")
    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {"title": "多源数据采集", "summary": f"采集 {len(collected)} 个源", "collected": collected},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
