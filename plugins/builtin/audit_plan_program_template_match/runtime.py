"""audit.plan.program-template-match: match a standard audit program
template to the requested areas.

Consumes a scheme-request (document-content) and emits a program-template
document with the matched procedures from the local library. Unmatched areas
are flagged 待人工匹配. Read-only.
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

PLUGIN_ID = "audit.plan.program-template-match"
CAPABILITY = "audit.plan.program-template-match"
_TEMPLATES = {
    "资金": [{"name": "资金收付核验", "detail": "对全部大额资金收付逐笔核验流向"}, {"name": "银行对账", "detail": "核对银行余额调节表"}],
    "采购": [{"name": "采购比价核验", "detail": "抽查采购订单与比价记录"}, {"name": "供应商准入", "detail": "核对供应商准入审批链"}],
    "销售": [{"name": "收入确认核查", "detail": "按准则核查收入确认时点"}, {"name": "回款核验", "detail": "核对应收账款回款周期"}],
    "资产": [{"name": "资产台账核验", "detail": "核对资产台账与实物状态"}],
    "存货": [{"name": "存货监盘", "detail": "执行实地监盘与差异分析"}],
}


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("scheme-request")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("scheme-request reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    body = payload.get("artifact")
    areas = body.get("areas") if isinstance(body, dict) else payload.get("areas")
    if not isinstance(areas, list):
        areas = body.get("scope_areas") if isinstance(body, dict) else None
    if not isinstance(areas, list) or not areas:
        raise InputRejected("scheme-request requires areas array")

    procedures = []
    unmatched = []
    for area in areas:
        key = str(area)
        matched = None
        for template_key, procs in _TEMPLATES.items():
            if template_key in key or key in template_key:
                matched = procs
                break
        if matched:
            procedures.extend({"area": key, **proc} for proc in matched)
        else:
            unmatched.append(key)
    if not procedures:
        raise InputRejected("scheme-request areas have no matched templates")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "审计程序模板",
            "summary": {"procedure_count": len(procedures), "unmatched_areas": unmatched},
            "procedures": procedures,
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
