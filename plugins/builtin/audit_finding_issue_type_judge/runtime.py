"""audit.finding.issue-type-judge: map anomaly candidates to issue types.

Consumes a merged suspicion set (anomaly-candidates contract), classifies each
candidate into a business issue category (合规/财务/内控/舞弊/资金/采购/销售/存货/费用)
and emits an issue-type-set under the finding-draft contract.  Read-only:
draft findings are not confirmations.
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

PLUGIN_ID = "audit.finding.issue-type-judge"
CAPABILITY = "audit.finding.issue-type-judge"
MAX_ISSUES = 2_000

# rule_key -> (category, description template, recommended action)
RULE_MAP: dict[str, tuple[str, str, str]] = {
    "duplicate_row": ("内控缺陷", "存在重复入账记录，入账控制缺失", "核查重复凭证并追溯审批链"),
    "invalid_date": ("费用跨期", "存在跨期或无效日期凭证，费用归属期间错误", "重分类至正确会计期间"),
    "out_of_period": ("费用跨期", "存在跨期或无效日期凭证，费用归属期间错误", "重分类至正确会计期间"),
    "unbalanced_entry": ("账务差错", "凭证借贷不平衡，账务记录存在差错", "核实原始单据并调整分录"),
    "outlier_amount": ("大额交易", "单笔金额超出异常阈值，交易真实性存疑", "执行大额交易穿透核查"),
    "large_amount": ("大额交易", "单笔金额超出异常阈值，交易真实性存疑", "执行大额交易穿透核查"),
    "round_amount": ("舞弊特征", "金额为整千且超过阈值，疑似人为调账", "检查调整分录审批与支持证据"),
    "negative_amount": ("资金异常", "存在负金额凭证，资金流转异常", "核实红字冲销与退款事项"),
    "missing_amount": ("数据质量", "金额字段缺失，数据完整性问题", "补齐凭证附件并重新核算"),
}


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("merged-suspicion")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("merged-suspicion reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    suspicion = read_artifact_json(artifact, roots)
    items = suspicion.get("candidates")
    if not isinstance(items, list):
        raise InputRejected("merged-suspicion is not an anomaly-candidates payload")

    findings: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if len(findings) >= MAX_ISSUES:
            break
        rule_key = str(item.get("rule_key") or "other")
        category, description, action = RULE_MAP.get(rule_key, ("其他", "未能归类的异常候选", "人工复核"))
        findings.append(
            {
                "issue_id": f"ISSUE-{index:04d}",
                "category": category,
                "rule_key": rule_key,
                "severity": item.get("severity", "low"),
                "row_ref": item.get("row_ref"),
                "source_ref": item.get("source_ref"),
                "description": description,
                "recommended_action": action,
                "score": item.get("score", 0.0),
            }
        )

    by_category: dict[str, int] = {}
    for finding in findings:
        by_category[finding["category"]] = by_category.get(finding["category"], 0) + 1

    return {
        "contract_id": "finding-draft",
        "contract_version": "1.0.0",
        "source_ref": suspicion.get("source_ref"),
        "summary": {"suspicion_count": len(items), "issue_count": len(findings), "by_category": by_category},
        "findings": findings,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
