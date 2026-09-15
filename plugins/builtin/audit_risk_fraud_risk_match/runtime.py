"""audit.risk.fraud-risk-match: match fraud-triangle signals (pressure /
opportunity / rationalization) to fraud risk candidates.

Consumes a fraud-input document (fraud_scenarios list with triangle flags)
and emits fraud-risk-set (anomaly-candidates) for scenarios where at least
two signals are present. Read-only.
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

PLUGIN_ID = "audit.risk.fraud-risk-match"
CAPABILITY = "audit.risk.fraud-risk-match"
MAX_ITEMS = 10_000
_SIGNALS = ("pressure", "opportunity", "rationalization")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("fraud-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("fraud-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    body = payload.get("artifact")
    scenarios = body.get("fraud_scenarios") if isinstance(body, dict) else payload.get("fraud_scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise InputRejected("fraud-input requires fraud_scenarios array")

    candidates = []
    for index, item in enumerate(scenarios[:MAX_ITEMS]):
        if not isinstance(item, dict):
            continue
        present = [s for s in _SIGNALS if item.get(s)]
        if len(present) < 2:
            continue
        candidates.append({
            "rule_id": f"FRD-{index + 1:04d}",
            "rule_key": "fraud_triangle",
            "severity": "high" if len(present) == 3 else "medium",
            "row_ref": f"fraud:{item.get('scenario_id') or index + 1}",
            "source_ref": "fraud-risk-match",
            "score": round(0.6 + 0.15 * len(present), 2),
            "note": f"舞弊场景「{item.get('name') or item.get('scenario') or index + 1}」"
                    f"命中三角信号：{'、'.join(present)}",
        })
    if not candidates:
        raise InputRejected("fraud-input produced no fraud-risk candidates")

    return {
        "contract_id": "anomaly-candidates", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026"),
        "summary": {"candidates": len(candidates)},
        "candidates": candidates,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
