# -*- coding: utf-8 -*-
"""audit.foundation.viz-analysis: render a metric-series into a viz artifact

Read-only/simulated support-layer plugin. No network, no writes outside the
staging artifact. Fails closed on invalid input.
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

PLUGIN_ID = "audit.foundation.viz-analysis"
CAPABILITY = "audit.foundation.viz-analysis"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("viz-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("viz-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    series = read_artifact_json(artifact, roots)
    points = series.get("points") if isinstance(series, dict) else None
    if not isinstance(points, list) or not points:
        raise InputRejected("viz-input requires points")
    chart = {"chart_type": "line", "series": str(series.get("series_id")), "points": points}
    return {
        "contract_id": "artifact-ref", "contract_version": "1.0.0",
        "artifact": {
            "uri": artifact.get("uri", ""), "sha256": artifact.get("sha256", "0" * 64),
            "size_bytes": artifact.get("size_bytes", 0),
            "chart": chart,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
