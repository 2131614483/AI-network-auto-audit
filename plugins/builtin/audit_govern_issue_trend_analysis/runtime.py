# -*- coding: utf-8 -*-
"""audit.govern.issue-trend-analysis: analyse multi-period issue trends

Read-only governance-layer plugin. No network, no writes outside the staging
artifact. Fails closed on invalid input.
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

PLUGIN_ID = "audit.govern.issue-trend-analysis"
CAPABILITY = "audit.govern.issue-trend-analysis"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("history-issue-set")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("history-issue-set reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    findings = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(findings, list) or not findings:
        raise InputRejected("history-issue-set requires findings array")

    by_category: dict[str, int] = {}
    high = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        cat = str(finding.get("category") or "其他")
        by_category[cat] = by_category.get(cat, 0) + 1
        if str(finding.get("severity")) == "high":
            high += 1
    top = sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": "issue-trend", "metric": "问题趋势", "unit": "count",
        "window_minutes": 10080,
        "points": [{"at": "2026-09-10T00:00:00+00:00", "value": len(findings)}],
        "summary": {
            "total": len(findings), "high": high,
            "top_categories": [{"category": c, "count": n} for c, n in top],
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
