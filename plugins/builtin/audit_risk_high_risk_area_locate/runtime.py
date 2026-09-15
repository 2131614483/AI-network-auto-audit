"""audit.risk.high-risk-area-locate: locate the high-risk business areas from
the assigned risk levels.

Consumes a risk-level metric series and emits a high-risk-area metric series
whose points label the concrete business area (from channel / row_ref) for
high and medium risks; low risks are excluded. Read-only.
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

PLUGIN_ID = "audit.risk.high-risk-area-locate"
CAPABILITY = "audit.risk.high-risk-area-locate"
_MAX_POINTS = 2_000
_AREA_MAP = {
    "政策合规": "资金收付", "行业对标": "采购合同", "内控流程": "资金收付",
    "财务异常": "资金收付", "业务流程": "采购合同", "舞弊特征": "资金收付",
}


def _area(channel: str, row_ref: str) -> str:
    for key, area in _AREA_MAP.items():
        if key in channel:
            return area
    if "proc" in row_ref:
        return "采购合同"
    return "资金收付"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("risk-level")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("risk-level reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    points = payload.get("points")
    if not isinstance(points, list) or not points:
        raise InputRejected("risk-level requires points array")

    areas = []
    for point in points[:_MAX_POINTS]:
        if not isinstance(point, dict):
            continue
        level = str(point.get("level") or "")
        if level not in ("high", "medium"):
            continue
        areas.append({
            "at": str(point.get("at") or "2026-01-01T00:00:00+08:00"),
            "value": float(point.get("value") or 0),
            "label": _area(str(point.get("channel") or ""), str(point.get("row_ref") or "")),
            "risk_id": str(point.get("risk_id") or ""),
            "level": level,
        })
    if not areas:
        raise InputRejected("risk-level contains no high/medium risk areas")

    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": f"high-risk-area-{payload.get('series_id') or '2026'}",
        "metric": "risk_score", "unit": "score", "window_minutes": 1440,
        "points": areas,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
