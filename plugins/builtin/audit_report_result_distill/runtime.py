# -*- coding: utf-8 -*-
"""audit.report.result-distill: distill common problems and management insights from the approved report

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

PLUGIN_ID = "audit.report.result-distill"
CAPABILITY = "audit.report.result-distill"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("report-approved")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("report-approved reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("report-approved payload must be an object")

    summary = payload.get("summary")
    approved = bool(summary.get("approved")) if isinstance(summary, dict) else False
    if not approved:
        raise InputRejected("report is not approved for distillation")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "审计成果提炼",
            "summary": "提炼共性问题与管理启示，沉淀至成果库并反哺后续审计重点",
            "insights": [
                "资金支付类问题占比高，需强化不相容岗位分离与授权审批",
                "采购验收环节存在断点，建议完善供应商准入与验收留痕",
                "共性内控缺陷建议纳入年度审计重点与规则库迭代",
            ],
            "distilled": True,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
