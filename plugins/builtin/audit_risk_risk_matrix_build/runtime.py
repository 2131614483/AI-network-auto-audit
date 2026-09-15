"""audit.risk.risk-matrix-build: aggregate multi-path risk candidates.

Consumes the six risk-scan channels (policy/industry/internal-control/finance/
process/fraud), each carrying anomaly-candidates, and produces a risk matrix
(metric-series contract): impact x probability scoring with high/medium/low
levels.  Read-only aggregation; the matrix is a planning projection.
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

PLUGIN_ID = "audit.risk.risk-matrix-build"
CAPABILITY = "audit.risk.risk-matrix-build"
CHANNELS = (
    ("policy-risk-set", "政策合规"),
    ("industry-risk-set", "行业对标"),
    ("ic-risk-set", "内控流程"),
    ("finance-anomaly-set", "财务异常"),
    ("process-gap-set", "业务流程"),
    ("fraud-risk-set", "舞弊特征"),
)
SEVERITY_IMPACT = {"high": 0.9, "medium": 0.5, "low": 0.2}
MAX_POINTS = 2_000


def _level(score: float) -> str:
    return "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    points: list[dict[str, Any]] = []
    channel_summary: dict[str, dict[str, Any]] = {}

    for port_id, channel_name in CHANNELS:
        port = envelope.get(port_id)
        if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
            raise InputRejected(f"{port_id} reference is missing")
        artifact = port["artifact"]
        read_verified_artifact(artifact, roots)
        candidates = read_artifact_json(artifact, roots)
        items = candidates.get("candidates")
        if not isinstance(items, list):
            raise InputRejected(f"{port_id} is not an anomaly-candidates payload")
        by_severity: dict[str, int] = {}
        for item in items:
            severity = str(item.get("severity") or "low")
            by_severity[severity] = by_severity.get(severity, 0) + 1
            impact = SEVERITY_IMPACT.get(severity, 0.2)
            probability = min(1.0, float(item.get("score") or 0.2))
            points.append(
                {
                    "risk_id": f"{port_id}:{item.get('rule_id', item.get('row_ref', '?'))}",
                    "channel": channel_name,
                    "rule_key": item.get("rule_key"),
                    "row_ref": item.get("row_ref"),
                    "source_ref": item.get("source_ref"),
                    "impact": impact,
                    "probability": probability,
                    "score": round(impact * probability, 4),
                    "level": _level(impact * probability),
                }
            )
            if len(points) >= MAX_POINTS:
                break
        channel_summary[port_id] = {"channel": channel_name, "candidates": len(items), "by_severity": by_severity}

    matrix = {"points": points, "high": 0, "medium": 0, "low": 0}
    for point in points:
        matrix[point["level"]] += 1

    return {
        "contract_id": "risk-matrix",
        "contract_version": "1.0.0",
        "channels": channel_summary,
        "matrix": matrix,
        "top_risks": sorted(points, key=lambda p: p["score"], reverse=True)[:5],
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
