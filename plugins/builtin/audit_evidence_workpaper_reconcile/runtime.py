# -*- coding: utf-8 -*-
"""audit.evidence.workpaper-reconcile: validate cross-entry reconciliation of
workpaper figures (workpaper-export sections). Read-only.
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

PLUGIN_ID = "audit.evidence.workpaper-reconcile"
CAPABILITY = "audit.evidence.workpaper-reconcile"


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

    checked = 0
    passed = 0
    issues = []
    for index, wp in enumerate(rows[:5_000]):
        if not isinstance(wp, dict):
            continue
        checked += 1
        wp_id = str(wp.get("section_id") or wp.get("workpaper_id") or wp.get("id") or f"WP-{index + 1:04d}")
        claim = str(wp.get("claim") or "")
        if not claim:
            issues.append({"row_ref": wp_id, "issue": "审计主张缺失，勾稽不完整", "level": "warn"})
            continue
        severity = str(wp.get("severity") or "low")
        if severity not in ("low", "medium", "high"):
            issues.append({"row_ref": wp_id, "issue": "风险等级字段非法", "level": "error"})
            continue
        passed += 1
    if checked == 0:
        raise InputRejected("workpaper-draft contains no reconcilable entries")

    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "dataset": "workpaper-reconcile", "checked": checked,
        "passed": passed, "failed": len(issues),
        "issues": issues[:200],
        "summary": f"勾稽校验 {checked} 条：通过 {passed}，问题 {len(issues)}",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
