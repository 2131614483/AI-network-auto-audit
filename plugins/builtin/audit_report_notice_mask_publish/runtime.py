# -*- coding: utf-8 -*-
"""audit.report.notice-mask-publish: publish a masked audit notice for internal disclosure

Read-only report-stage plugin. No network, no writes outside the staging
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

PLUGIN_ID = "audit.report.notice-mask-publish"
CAPABILITY = "audit.report.notice-mask-publish"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("report-approved")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("report-approved reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("report-approved payload must be an object")

    summary = payload.get("summary")
    approved = bool(summary.get("approved")) if isinstance(summary, dict) else False
    if not approved:
        raise InputRejected("report is not approved for publishing")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "审计公告（脱敏发布）",
            "summary": "已对报告内容脱敏后对内发布，涉及金额与人名均作掩码处理",
            "notice": "【审计公告】本轮专项审计已完成，相关问题已下发整改任务并跟踪销号。",
            "masked": True,
            "mask_fields": ["amount", "person_name", "evidence_uri"],
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
