# -*- coding: utf-8 -*-
"""audit.evidence.workpaper-template-update: update workpaper templates against
the latest standard (workpaper-export sections). Read-only.
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

PLUGIN_ID = "audit.evidence.workpaper-template-update"
CAPABILITY = "audit.evidence.workpaper-template-update"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("workpaper-draft")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("workpaper-draft reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)

    rows = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = payload.get("workpapers") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise InputRejected("workpaper-draft requires sections array")

    updates = []
    for index, wp in enumerate(rows[:5_000]):
        if not isinstance(wp, dict):
            continue
        wp_id = str(wp.get("section_id") or wp.get("workpaper_id") or wp.get("id") or f"WP-{index + 1:04d}")
        updates.append({
            "template_id": f"TMPL-{wp_id}",
            "standard_version": "2026.1",
            "action": "align",
            "fields": ["risk_assessment", "evidence_index", "conclusion"],
        })
    if not updates:
        raise InputRejected("workpaper-draft contains no templated entries")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026") if isinstance(payload, dict) else "2026",
        "artifact": {
            "title": "底稿模板更新",
            "summary": f"按准则 2026.1 更新 {len(updates)} 套底稿模板",
            "template_updates": updates,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
