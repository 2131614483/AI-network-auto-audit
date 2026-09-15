# -*- coding: utf-8 -*-
"""audit.govern.effect-evaluate: evaluate audit effect value from the closed remedy ledger

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

PLUGIN_ID = "audit.govern.effect-evaluate"
CAPABILITY = "audit.govern.effect-evaluate"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("remedy-ledger")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("remedy-ledger reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    ledger = read_artifact_json(artifact, roots)
    if not isinstance(ledger, dict):
        raise InputRejected("remedy-ledger payload must be an object")

    summary = ledger.get("summary")
    closed = bool(summary.get("closed")) if isinstance(summary, dict) else False
    nodes = ledger.get("nodes") if isinstance(ledger.get("nodes"), list) else []
    closed_count = sum(1 for n in nodes if isinstance(n, dict) and n.get("status") == "closed")
    total = len(nodes)
    effect = round(closed_count / total, 6) if total else (1.0 if closed else 0.0)
    return {
        "contract_id": "metric-series", "contract_version": "1.0.0",
        "series_id": "audit-effect-value", "metric": "审计成效", "unit": "ratio",
        "window_minutes": 10080,
        "points": [{"at": "2026-09-10T00:00:00+00:00", "value": effect}],
        "summary": {"effect_ratio": effect, "closed": closed_count, "total": total},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
