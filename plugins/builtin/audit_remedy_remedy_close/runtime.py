# -*- coding: utf-8 -*-
"""audit.remedy.remedy-close: close remedy tasks whose verification passed; else keep open

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

PLUGIN_ID = "audit.remedy.remedy-close"
CAPABILITY = "audit.remedy.remedy-close"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("remedy-verdict")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("remedy-verdict reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    verdict = read_artifact_json(artifact, roots)
    if not isinstance(verdict, dict):
        raise InputRejected("remedy-verdict payload must be an object")

    summary = verdict.get("summary")
    valid = bool(summary.get("valid")) if isinstance(summary, dict) else False
    violations = verdict.get("violations") if isinstance(verdict.get("violations"), list) else []
    closed = valid and not violations
    return {
        "contract_id": "workflow", "contract_version": "1.0.0",
        "workflow_id": "remedy-close-2026",
        "kind": "remedy-close",
        "nodes": [{"task_id": "RT-0001", "status": "closed" if closed else "open"}],
        "summary": {
            "closed": closed,
            "violation_count": len(violations),
            "note": "整改销号完成" if closed else "整改验证未通过，暂缓销号",
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
