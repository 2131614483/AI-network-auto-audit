"""audit.risk.risk-advice-generate: generate risk-response advice for the
high-risk areas.

Consumes a high-risk-area metric series and emits a risk-advice-set document
(artifact.advices) matching local advice templates per area. Unmatched areas
are flagged 待人工补充. Read-only.
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

PLUGIN_ID = "audit.risk.risk-advice-generate"
CAPABILITY = "audit.risk.risk-advice-generate"
_MAX_POINTS = 2_000
_ADVICE = {
    "资金收付": "重点核查大额资金流向与异常支付，执行凭证穿透与函证核验",
    "采购合同": "对采购合同执行全量比价核验与供应商准入核查",
    "存货": "执行实地监盘与账实差异分析",
    "销售回款": "核对应收账款回款周期与收入确认时点",
}
_DEFAULT = "针对该高风险领域执行专项核查程序并留存证据链"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("high-risk-area")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("high-risk-area reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    points = payload.get("points")
    if not isinstance(points, list) or not points:
        raise InputRejected("high-risk-area requires points array")

    seen: list[str] = []
    advices = []
    for point in points[:_MAX_POINTS]:
        if not isinstance(point, dict):
            continue
        area = str(point.get("label") or "")
        if not area or area in seen:
            continue
        seen.append(area)
        advices.append({
            "area": area,
            "advice": _ADVICE.get(area, _DEFAULT),
            "source": "risk-advice-generate",
        })
    if not advices:
        raise InputRejected("high-risk-area contains no areas")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "风险应对建议",
            "summary": {"advice_count": len(advices)},
            "advices": advices,
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
