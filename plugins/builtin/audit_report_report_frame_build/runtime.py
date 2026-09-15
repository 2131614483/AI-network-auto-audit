# -*- coding: utf-8 -*-
"""audit.report.report-frame-build: build the report frame (chapter skeleton) from the project snapshot

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

PLUGIN_ID = "audit.report.report-frame-build"
CAPABILITY = "audit.report.report-frame-build"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("project-snapshot")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("project-snapshot reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("project-snapshot payload must be an object")

    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    projects = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        msg = str(check.get("message") or "")
        if not msg.startswith("project:"):
            continue
        parts = {}
        for token in msg.split():
            if ":" in token:
                k, _, v = token.partition(":")
                parts[k] = v
        projects.append({
            "project_id": str(parts.get("project") or f"PR-{len(projects) + 1:04d}"),
            "score": float(parts["score"]) if parts.get("score") else 0.0,
            "admitted": str(parts.get("admitted") or "true") == "true",
        })
    if not projects:
        raise InputRejected("project-snapshot contains no admitted projects")

    sections = ["封面", "审计概况", "审计依据与范围", "主要审计发现", "问题定性", "审计建议", "整改要求", "审计结论"]
    expert = payload.get("expert") if isinstance(payload.get("expert"), dict) else None
    return {
        "contract_id": "report-draft", "contract_version": "1.0.0",
        "report_kind": "frame", "status": "frame",
        "engagement_id": str(payload.get("snapshot_sha256") or "0" * 64)[:32],
        "engagement_name": f"资金舞弊专项审计（{projects[0]['project_id']}）",
        "template_version": "2026.1.0",
        "sections": sections,
        "projects": projects,
        "expert_profile": str(expert.get("expert_profile") or "") if expert else "",
        "summary": {"project_count": len(projects), "frame_sections": len(sections)},
        "constraints": {"human_issuance_required": True},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
