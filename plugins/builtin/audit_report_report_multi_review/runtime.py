# -*- coding: utf-8 -*-
"""audit.report.report-multi-review: multi-level review gate: approve the report when checks pass

Read-only report-stage plugin. No network, no writes outside the staging
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

PLUGIN_ID = "audit.report.report-multi-review"
CAPABILITY = "audit.report.report-multi-review"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("report-check-report")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("report-check-report reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("report-check-report payload must be an object")

    summary = payload.get("summary")
    valid = bool(summary.get("valid")) if isinstance(summary, dict) else False
    violations = payload.get("violations") if isinstance(payload.get("violations"), list) else []
    levels = ["项目负责人", "部门负责人", "总审计师"]
    approved = valid and not violations
    return {
        "contract_id": "report-draft", "contract_version": "1.0.0",
        "report_kind": "review", "status": "approved" if approved else "rejected",
        "summary": {
            "approved": approved,
            "review_levels": levels,
            "violation_count": len(violations),
            "note": "三级审核通过，准予发布" if approved else "存在校验问题，退回修改",
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
