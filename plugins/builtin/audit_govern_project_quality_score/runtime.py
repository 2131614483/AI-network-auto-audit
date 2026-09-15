# -*- coding: utf-8 -*-
"""audit.govern.project-quality-score: score the whole audit project flow quality

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

PLUGIN_ID = "audit.govern.project-quality-score"
CAPABILITY = "audit.govern.project-quality-score"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("project-flow-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("project-flow-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("project-flow-input payload must be an object")

    artifact_body = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else payload
    checks = artifact_body.get("checks") or payload.get("checks")
    total = float(artifact_body.get("total_checks") or 0)
    passed = float(artifact_body.get("passed_checks") or 0)
    if isinstance(checks, list):
        total = float(len(checks))
        passed = float(sum(1 for c in checks if isinstance(c, dict) and c.get("status") == "pass"))
    score = round(passed / total, 6) if total else 0.0
    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": "audit-project-quality", "metric": "项目质量分", "unit": "score",
        "window_minutes": 10080,
        "points": [{"at": "2026-09-10T00:00:00+00:00", "value": score}],
        "summary": {"score": score, "passed": int(passed), "total": int(total)},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
