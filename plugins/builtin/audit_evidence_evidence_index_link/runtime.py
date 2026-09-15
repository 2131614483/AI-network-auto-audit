# -*- coding: utf-8 -*-
"""audit.evidence.evidence-index-link: link workpaper sections to evidence items
as an evidence-lineage graph (nodes + edges) for evidence-verify.

Read-only; fails closed on invalid input.
"""
from __future__ import annotations

import hashlib
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.evidence.evidence-index-link"
CAPABILITY = "audit.evidence.evidence-index-link"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    wp_port = envelope.get("workpaper-draft")
    ev_port = envelope.get("photo-evidence")
    if not isinstance(wp_port, dict) or not isinstance(wp_port.get("artifact"), dict):
        raise InputRejected("workpaper-draft reference is missing")
    if not isinstance(ev_port, dict) or not isinstance(ev_port.get("artifact"), dict):
        raise InputRejected("photo-evidence reference is missing")
    roots = allowed_roots()
    read_verified_artifact(wp_port["artifact"], roots)
    wp_payload = read_artifact_json(wp_port["artifact"], roots)
    read_verified_artifact(ev_port["artifact"], roots)
    ev_payload = read_artifact_json(ev_port["artifact"], roots)

    wp_rows = wp_payload.get("sections") if isinstance(wp_payload, dict) else None
    if not isinstance(wp_rows, list):
        wp_rows = wp_payload.get("workpapers") if isinstance(wp_payload, dict) else None
    if not isinstance(wp_rows, list) or not wp_rows:
        raise InputRejected("workpaper-draft requires sections array")

    ev_ref = str(ev_payload.get("uri") or "") if isinstance(ev_payload, dict) else ""
    ev_id = str(ev_payload.get("evidence_id") or ev_payload.get("id") or "EV-0001")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for index, wp in enumerate(wp_rows[:5_000]):
        if not isinstance(wp, dict):
            continue
        wp_id = str(wp.get("section_id") or wp.get("workpaper_id") or wp.get("id") or f"WP-{index + 1:04d}")
        node_id = f"workpaper:{wp_id}"
        nodes.append({
            "node_id": node_id,
            "kind": "workpaper",
            "sha256": hashlib.sha256(node_id.encode("utf-8")).hexdigest(),
            "title": str(wp.get("title") or f"底稿{index + 1}"),
        })
    ev_node = f"evidence:{ev_id}"
    nodes.append({
        "node_id": ev_node,
        "kind": "evidence",
        "sha256": hashlib.sha256((ev_node + ev_ref).encode("utf-8")).hexdigest(),
        "uri": ev_ref,
    })
    for index, wp in enumerate(wp_rows[:5_000]):
        if not isinstance(wp, dict):
            continue
        wp_id = str(wp.get("section_id") or wp.get("workpaper_id") or wp.get("id") or f"WP-{index + 1:04d}")
        edges.append({
            "edge_id": f"idx-{index + 1:04d}",
            "source": f"workpaper:{wp_id}",
            "target": ev_node,
            "relation": "evidences",
        })
    if not nodes or not edges:
        raise InputRejected("workpaper-draft contains no linkable entries")

    return {
        "contract_id": "evidence-lineage", "contract_version": "1.0.0",
        "lineage_id": f"lineage-{ev_id}",
        "period": str(wp_payload.get("period") or "2026") if isinstance(wp_payload, dict) else "2026",
        "entity": "audit-workpaper",
        "nodes": nodes,
        "edges": edges,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
