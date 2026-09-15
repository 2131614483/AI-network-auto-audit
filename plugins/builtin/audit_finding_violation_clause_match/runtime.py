"""audit.finding.violation-clause-match: map issue types to regulation clauses.

Each draft finding is matched against a built-in clause library keyed by
issue category (合规/财务/内控/舞弊/资金/采购/销售/存货/费用/其他), enriching
the finding-draft payload with clause_id / regulation / clause_text.  The
clause library is a deterministic local table; it is not a live regulation
feed.  Read-only.
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

PLUGIN_ID = "audit.finding.violation-clause-match"
CAPABILITY = "audit.finding.violation-clause-match"
MAX_ISSUES = 2_000

# category -> (clause_id, regulation, clause_text)
CLAUSE_LIBRARY: dict[str, tuple[str, str, str]] = {
    "合规": ("CL-REG-01", "企业内部控制基本规范", "企业应当建立并执行合规审查程序，确保经营活动符合法律法规要求"),
    "财务": ("CL-FIN-02", "企业会计准则——基本准则", "企业应当如实反映交易或者事项，会计信息应当真实、完整、可比"),
    "内控缺陷": ("CL-IC-03", "企业内部控制基本规范", "企业应当针对关键控制点设置不相容职务分离与授权审批控制"),
    "舞弊特征": ("CL-FRD-04", "反舞弊管理制度", "企业应当建立反舞弊机制，对异常交易开展穿透核查"),
    "资金异常": ("CL-CAP-05", "资金管理办法", "资金收付应当经过授权审批，大额资金变动应当留痕可追溯"),
    "采购违规": ("CL-PUR-06", "采购管理制度", "采购应当履行询比价与供应商准入程序，禁止违规利益输送"),
    "销售异常": ("CL-SAL-07", "销售管理制度", "销售折扣与退货应当依据合同执行，收入确认应当符合准则"),
    "存货问题": ("CL-INV-08", "存货管理制度", "存货应当定期盘点，账实差异应当查明原因并调整"),
    "费用违规": ("CL-EXP-09", "费用报销管理办法", "费用报销应当附真实有效凭证，超标准支出应当履行审批"),
    "数据质量": ("CL-DQ-10", "数据治理规范", "业务数据应当完整、准确、一致，关键数据缺失应当及时补录"),
    "其他": ("CL-OTH-99", "审计工作规范", "对未能归类的异常事项应当补充核查并保留工作底稿"),
}


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("issue-type-set")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("issue-type-set reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise InputRejected("issue-type-set is not a finding-draft payload")

    enriched: list[dict[str, Any]] = []
    for finding in findings:
        if len(enriched) >= MAX_ISSUES:
            break
        category = str(finding.get("category") or "其他")
        clause_id, regulation, clause_text = CLAUSE_LIBRARY.get(category, CLAUSE_LIBRARY["其他"])
        item = dict(finding)
        item["clause_id"] = clause_id
        item["regulation"] = regulation
        item["clause_text"] = clause_text
        enriched.append(item)

    summary = dict(payload.get("summary") or {})
    summary["clause_matched"] = len(enriched)
    return {
        "contract_id": "finding-draft",
        "contract_version": "1.0.0",
        "source_ref": payload.get("source_ref"),
        "summary": summary,
        "findings": enriched,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
