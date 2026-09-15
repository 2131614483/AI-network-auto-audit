# -*- coding: utf-8 -*-
"""audit.govern.case-library-update: ingest distilled results as case-library entries.

Read-only governance-layer plugin. No network, no writes outside the staging
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

PLUGIN_ID = "audit.govern.case-library-update"
CAPABILITY = "audit.govern.case-library-update"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("distilled-result")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("distilled-result reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("distilled-result payload must be an object")

    body = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else payload
    insights = body.get("insights") if isinstance(body, dict) else None
    if not isinstance(insights, list) or not insights:
        raise InputRejected("distilled-result requires insights")

    entries = [
        {"case_id": f"CASE-{i + 1:03d}", "summary": str(insight)}
        for i, insight in enumerate(insights[:50])
        if isinstance(insight, str) and insight
    ]
    if not entries:
        raise InputRejected("no usable insights to ingest")
    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "案例库更新",
            "summary": f"新增审计案例 {len(entries)} 条",
            "entries": entries,
            "updated": True,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
