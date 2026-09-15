"""audit.risk.macro-policy-risk-scan: scan macro policy / regulation input for
compliance risk candidates.

Consumes a policy-input document (list of policy/regulation items) and emits
policy-risk-set (anomaly-candidates) for items whose keywords hit the local
audit-risk lexicon. Unmatched items are ignored (no invented risk). Read-only.
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

PLUGIN_ID = "audit.risk.macro-policy-risk-scan"
CAPABILITY = "audit.risk.macro-policy-risk-scan"
RISK_LEXICON = ("资金", "采购", "销售", "合同", "费用", "内控", "数据", "票据", "税务", "关联")
MAX_ITEMS = 10_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("policy-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("policy-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    body = payload.get("artifact")
    policies = body.get("policies") if isinstance(body, dict) else payload.get("policies")
    if not isinstance(policies, list) or not policies:
        raise InputRejected("policy-input requires policies array")

    candidates = []
    for index, item in enumerate(policies[:MAX_ITEMS]):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "")
        body_text = str(item.get("content") or "")
        hits = [kw for kw in RISK_LEXICON if kw in title or kw in body_text]
        if not hits:
            continue
        candidates.append({
            "rule_id": f"POLICY-{index + 1:04d}",
            "rule_key": "policy_breach",
            "severity": "high" if any(h in ("资金", "关联") for h in hits) else "medium",
            "row_ref": f"policy:{item.get('policy_id') or index + 1}",
            "source_ref": "macro-policy-risk-scan",
            "score": round(0.5 + 0.1 * min(len(hits), 4), 2),
            "note": f"政策条款含风险关键词：{'、'.join(hits)}",
        })
    if not candidates:
        raise InputRejected("policy-input produced no policy risk candidates")

    return {
        "contract_id": "anomaly-candidates", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026"),
        "summary": {"candidates": len(candidates)},
        "candidates": candidates,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
