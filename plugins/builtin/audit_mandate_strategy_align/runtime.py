"""audit.mandate.strategy-align: align collected demands with the audit
strategy keywords.

Consumes a demand-set (document-content) and emits an aligned-demand with a
deterministic alignment level: demands whose title/detail carry strategy
markers are aligned, others are flagged 待人工确认. Read-only.
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

PLUGIN_ID = "audit.mandate.strategy-align"
CAPABILITY = "audit.mandate.strategy-align"
_ALIGN_MARKERS = ("资金", "舞弊", "合规", "监管", "内控", "高风险", "采购", "销售", "重大")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("demand-set")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("demand-set reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    artifact_body = payload.get("artifact")
    demands = artifact_body.get("demands") if isinstance(artifact_body, dict) else payload.get("demands")
    if not isinstance(demands, list):
        raise InputRejected("demand-set requires demands array")

    aligned = []
    for demand in demands:
        if not isinstance(demand, dict):
            continue
        text = f"{demand.get('title') or ''} {demand.get('detail') or ''}"
        markers = [m for m in _ALIGN_MARKERS if m in text]
        aligned.append({
            **{k: v for k, v in demand.items()},
            "alignment": "high" if markers else "待人工确认",
            "aligned_markers": markers,
            "alignment_basis": "strategy_keyword_match" if markers else "human_review",
        })
    if not aligned:
        raise InputRejected("demand-set contains no demands")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "战略对齐后的审计需求",
            "summary": {"demand_count": len(aligned),
                        "aligned_high": sum(1 for a in aligned if a["alignment"] == "high"),
                        "pending_human": sum(1 for a in aligned if a["alignment"] == "待人工确认")},
            "demands": aligned,
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
