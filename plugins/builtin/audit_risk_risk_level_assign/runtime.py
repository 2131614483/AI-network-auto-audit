"""audit.risk.risk-level-assign: assign risk levels to the risk-matrix points.

Consumes a risk-matrix payload (metric-series contract) and emits a
risk-level metric series where each point carries an explicit level
(high/medium/low) derived from the score thresholds. Read-only.
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

PLUGIN_ID = "audit.risk.risk-level-assign"
CAPABILITY = "audit.risk.risk-level-assign"
MAX_POINTS = 2_000


def _level(score: float) -> str:
    return "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("risk-matrix")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("risk-matrix reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)

    points = payload.get("points")
    if not isinstance(points, list):
        points = payload.get("matrix", {}).get("points") if isinstance(payload.get("matrix"), dict) else None
    if not isinstance(points, list) or not points:
        raise InputRejected("risk-matrix requires points array")

    out_points = []
    for point in points[:MAX_POINTS]:
        if not isinstance(point, dict):
            continue
        try:
            score = float(point.get("score") or 0)
        except (TypeError, ValueError):
            continue
        out_points.append({
            "at": "2026-01-01T00:00:00+08:00",
            "value": round(score, 4),
            "risk_id": str(point.get("risk_id") or ""),
            "channel": str(point.get("channel") or ""),
            "row_ref": str(point.get("row_ref") or ""),
            "level": _level(score),
        })
    if not out_points:
        raise InputRejected("risk-matrix contains no scorable points")

    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": f"risk-level-{payload.get('period') or '2026'}",
        "metric": "risk_score", "unit": "score", "window_minutes": 1440,
        "points": out_points,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
