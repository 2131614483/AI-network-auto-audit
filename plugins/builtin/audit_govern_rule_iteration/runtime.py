# -*- coding: utf-8 -*-
"""audit.govern.rule-iteration: iterate the rule pack based on the trend report

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

PLUGIN_ID = "audit.govern.rule-iteration"
CAPABILITY = "audit.govern.rule-iteration"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("trend-report")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("trend-report reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    trend = read_artifact_json(artifact, roots)
    if not isinstance(trend, dict):
        raise InputRejected("trend-report payload must be an object")

    summary = trend.get("summary")
    top = summary.get("top_categories") if isinstance(summary, dict) else None
    rules = []
    if isinstance(top, list):
        for item in top:
            if isinstance(item, dict) and item.get("category"):
                rules.append({
                    "rule_id": f"RULE-{item['category']}",
                    "target": str(item["category"]),
                    "weight": float(item.get("count") or 1),
                })
    if not rules:
        raise InputRejected("trend-report has no top categories to iterate")

    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": "0" * 64,
        "summary": {"valid": True, "checked_columns": len(rules), "checked_rows": len(rules), "missing_values": 0},
        "checks": [{"check_id": "columns", "status": "pass", "message": f"规则迭代 {len(rules)} 条"}],
        "violations": [],
        "rules": rules,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
