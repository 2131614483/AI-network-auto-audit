# -*- coding: utf-8 -*-
"""audit.foundation.lineage-track: build an evidence lineage graph from a query

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

PLUGIN_ID = "audit.foundation.lineage-track"
CAPABILITY = "audit.foundation.lineage-track"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("lineage-query")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("lineage-query reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    nodes = payload.get("nodes") if isinstance(payload, dict) else None
    edges = payload.get("edges") if isinstance(payload, dict) else None
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise InputRejected("lineage-query requires nodes and edges")
    return {
        "contract_id": "evidence-lineage", "contract_version": "1.0.0",
        "lineage_id": "L-lineage", "nodes": nodes, "edges": edges,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
