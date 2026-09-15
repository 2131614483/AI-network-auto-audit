# -*- coding: utf-8 -*-
"""audit.evidence.workpaper-review3: drive three-level review
(preparer -> manager -> head) of workpaper sections. Read-only.
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

PLUGIN_ID = "audit.evidence.workpaper-review3"
CAPABILITY = "audit.evidence.workpaper-review3"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    wp_port = envelope.get("workpaper-draft")
    rc_port = envelope.get("reconcile-report")
    if not isinstance(wp_port, dict) or not isinstance(wp_port.get("artifact"), dict):
        raise InputRejected("workpaper-draft reference is missing")
    if not isinstance(rc_port, dict) or not isinstance(rc_port.get("artifact"), dict):
        raise InputRejected("reconcile-report reference is missing")
    roots = allowed_roots()
    read_verified_artifact(wp_port["artifact"], roots)
    payload = read_artifact_json(wp_port["artifact"], roots)
    read_verified_artifact(rc_port["artifact"], roots)
    rc = read_artifact_json(rc_port["artifact"], roots)

    rows = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = payload.get("workpapers") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise InputRejected("workpaper-draft requires sections array")

    rc_failed = rc.get("failed") if isinstance(rc, dict) else 0
    levels = ["preparer", "manager", "head"]
    statuses = []
    for index, wp in enumerate(rows[:5_000]):
        if not isinstance(wp, dict):
            continue
        wp_id = str(wp.get("section_id") or wp.get("workpaper_id") or wp.get("id") or f"WP-{index + 1:04d}")
        statuses.append({
            "workpaper_id": wp_id,
            "levels": levels,
            "current": "head" if rc_failed == 0 else "preparer",
            "approved": rc_failed == 0,
            "note": "勾稽全部通过" if rc_failed == 0 else "存在勾稽问题，退回编制人",
        })
    if not statuses:
        raise InputRejected("workpaper-draft contains no reviewable entries")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026") if isinstance(payload, dict) else "2026",
        "artifact": {
            "title": "底稿三级复核",
            "summary": f"复核 {len(statuses)} 份底稿：{'全部通过三级复核' if rc_failed == 0 else '退回编制人'}"
                       f"（勾稽问题 {rc_failed}）",
            "review_status": statuses,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
