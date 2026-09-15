# -*- coding: utf-8 -*-
"""audit.report.advice-match: match audit advice templates to the final issue set

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

PLUGIN_ID = "audit.report.advice-match"
CAPABILITY = "audit.report.advice-match"

ADVICE_LIBRARY = {
    "财务核算": "完善财务核算流程，对相关账务进行重新审定并补充凭证。",
    "资金管理": "加强资金审批与流向监控，落实不相容岗位分离，定期对账。",
    "采购管理": "强化供应商准入与验收管理，完善合同履约跟踪机制。",
    "销售管理": "规范销售折扣与返利审批，加强客户信用管理。",
    "内控缺陷": "补齐内部控制缺陷点，明确岗位职责与审批权限，落实自查自纠。",
    "舞弊风险": "开展专项排查，移交线索并强化舞弊防控与举报渠道建设。",
    "其他": "按审计整改管理办法限期整改，整改完成后复核销号。",
}


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("final-issue-set")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("final-issue-set reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    findings = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(findings, list) or not findings:
        raise InputRejected("final-issue-set requires findings array")

    advices = []
    for index, finding in enumerate(findings[:2_000]):
        if not isinstance(finding, dict):
            continue
        category = str(finding.get("category") or "其他")
        advice = ADVICE_LIBRARY.get(category, ADVICE_LIBRARY["其他"])
        advices.append({
            "issue_id": str(finding.get("issue_id") or f"ISSUE-{index + 1:04d}"),
            "category": category,
            "severity": str(finding.get("severity") or "low"),
            "advice": advice,
        })
    if not advices:
        raise InputRejected("final-issue-set contains no matching advice")

    return {
        "contract_id": "report-draft", "contract_version": "1.0.0",
        "report_kind": "advice", "status": "draft",
        "summary": {"advice_count": len(advices)},
        "advices": advices,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
