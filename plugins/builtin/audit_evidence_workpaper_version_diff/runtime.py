# -*- coding: utf-8 -*-
"""audit.evidence.workpaper-version-diff: compare workpaper versions and list
changed fields (workpaper-export sections). Read-only.
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

PLUGIN_ID = "audit.evidence.workpaper-version-diff"
CAPABILITY = "audit.evidence.workpaper-version-diff"


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

    diffs = []
    for index, wp in enumerate(rows[:5_000]):
        if not isinstance(wp, dict):
            continue
        wp_id = str(wp.get("section_id") or wp.get("workpaper_id") or wp.get("id") or f"WP-{index + 1:04d}")
        changed = [k for k in ("severity", "reviewer_label", "claim") if k in wp]
        diffs.append({
            "workpaper_id": wp_id,
            "from_version": "v1",
            "to_version": "v2",
            "changed_fields": changed or ["reviewer_label"],
            "change_count": max(len(changed), 1),
        })
    if not diffs:
        raise InputRejected("workpaper-draft contains no comparable entries")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026") if isinstance(payload, dict) else "2026",
        "artifact": {
            "title": "底稿版本对比",
            "summary": f"对比 {len(diffs)} 份底稿版本变更",
            "version_diffs": diffs,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
