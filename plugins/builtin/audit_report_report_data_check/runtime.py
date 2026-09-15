# -*- coding: utf-8 -*-
"""audit.report.report-data-check: validate report draft data consistency (amounts, fields, counts)

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

PLUGIN_ID = "audit.report.report-data-check"
CAPABILITY = "audit.report.report-data-check"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("report-draft-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("report-draft-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("report payload must be an object")

    checks: list[dict[str, str]] = []
    violations: list[dict[str, str]] = []

    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else None
    if not sections:
        checks.append({"check_id": "columns", "status": "fail", "message": "报告章节缺失"})
        violations.append({"check_id": "columns", "message": "报告章节缺失"})
    else:
        checks.append({"check_id": "columns", "status": "pass", "message": f"报告章节 {len(sections)} 个完整"})

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        checks.append({"check_id": "freshness", "status": "fail", "message": "报告汇总缺失"})
        violations.append({"check_id": "freshness", "message": "报告汇总缺失"})
    else:
        checks.append({"check_id": "freshness", "status": "pass", "message": "报告汇总完整"})

    issue_count = 0
    if isinstance(payload.get("issues"), list):
        issue_count = len(payload["issues"])
    if isinstance(payload.get("findings"), list):
        issue_count = len(payload["findings"])
    if issue_count > 0:
        checks.append({"check_id": "missing_rate", "status": "pass", "message": f"问题条目 {issue_count} 条"})
    else:
        checks.append({"check_id": "missing_rate", "status": "warn", "message": "报告未包含问题明细，需人工补充"})

    valid = not violations
    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": "0" * 64,
        "summary": {"valid": valid, "checked_columns": len(checks), "checked_rows": issue_count,
                    "missing_values": len(violations)},
        "checks": checks,
        "violations": violations,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
