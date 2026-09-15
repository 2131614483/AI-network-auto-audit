"""audit.risk.industry-risk-benchmark: benchmark industry high-risk events to
emit industry risk candidates.

Consumes an industry-benchmark document (industry_risks list of known
high-incidence issues) and emits industry-risk-set (anomaly-candidates).
Items pass through as candidates without invented detail. Read-only.
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

PLUGIN_ID = "audit.risk.industry-risk-benchmark"
CAPABILITY = "audit.risk.industry-risk-benchmark"
MAX_ITEMS = 10_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("industry-benchmark")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("industry-benchmark reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    body = payload.get("artifact")
    risks = body.get("industry_risks") if isinstance(body, dict) else payload.get("industry_risks")
    if not isinstance(risks, list) or not risks:
        raise InputRejected("industry-benchmark requires industry_risks array")

    candidates = []
    for index, item in enumerate(risks[:MAX_ITEMS]):
        if not isinstance(item, dict):
            continue
        candidates.append({
            "rule_id": f"IND-{index + 1:04d}",
            "rule_key": "industry_benchmark",
            "severity": str(item.get("severity") or "medium"),
            "row_ref": f"industry:{item.get('risk_id') or index + 1}",
            "source_ref": "industry-risk-benchmark",
            "score": round(float(item.get("score") or 0.5), 2),
            "note": str(item.get("name") or item.get("title") or "行业高发风险"),
        })
    if not candidates:
        raise InputRejected("industry-benchmark produced no candidates")

    return {
        "contract_id": "anomaly-candidates", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026"),
        "summary": {"candidates": len(candidates)},
        "candidates": candidates,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
