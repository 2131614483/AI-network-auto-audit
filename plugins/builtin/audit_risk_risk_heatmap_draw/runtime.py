"""audit.risk.risk-heatmap-draw: draw a channel x level risk heatmap payload.

Consumes a risk-level metric series and emits a risk-heatmap document with a
channel-by-level matrix and the overall risk density. Read-only.
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

PLUGIN_ID = "audit.risk.risk-heatmap-draw"
CAPABILITY = "audit.risk.risk-heatmap-draw"
_MAX_POINTS = 2_000
_LEVELS = ("high", "medium", "low")


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

    channels: dict[str, dict[str, int]] = {}
    for point in points[:_MAX_POINTS]:
        if not isinstance(point, dict):
            continue
        channel = str(point.get("channel") or "未知通道")
        level = str(point.get("level") or "low")
        if level not in _LEVELS:
            level = "low"
        cell = channels.setdefault(channel, {lvl: 0 for lvl in _LEVELS})
        cell[level] += 1

    total = sum(sum(cell.values()) for cell in channels.values())
    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "风险热图",
            "levels": list(_LEVELS),
            "matrix": {channel: cell for channel, cell in sorted(channels.items())},
            "summary": {"total_risks": total, "channel_count": len(channels),
                        "high_total": sum(c["high"] for c in channels.values())},
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
