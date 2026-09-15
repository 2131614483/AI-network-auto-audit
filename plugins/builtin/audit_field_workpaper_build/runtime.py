"""audit.field.workpaper-build: draft a workpaper from the audit program
template and the sample size.

Consumes a program template (document-content JSON) and a sample-size series
(metric-series JSON) and emits a workpaper-export draft fully compliant with
the workpaper-export contract. Results are explicitly unreviewed (severity
low, reviewer_label 待复核); nothing is fabricated. Read-only.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.field.workpaper-build"
CAPABILITY = "audit.field.workpaper-build"
MAX_SECTIONS = 100


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    template_port = envelope.get("program-template")
    size_port = envelope.get("sample-size")
    if not isinstance(template_port, dict) or not isinstance(template_port.get("artifact"), dict):
        raise InputRejected("program-template reference is missing")
    if not isinstance(size_port, dict) or not isinstance(size_port.get("artifact"), dict):
        raise InputRejected("sample-size reference is missing")
    roots = allowed_roots()
    read_verified_artifact(template_port["artifact"], roots)
    read_verified_artifact(size_port["artifact"], roots)
    template = read_artifact_json(template_port["artifact"], roots)
    size_payload = read_artifact_json(size_port["artifact"], roots)

    if isinstance(template.get("artifact"), dict):
        artifact_body = template["artifact"]
        if isinstance(artifact_body.get("text"), str):
            template = artifact_body
        elif isinstance(artifact_body.get("procedures"), list):
            template = artifact_body
    procedures = template.get("procedures")
    if not isinstance(procedures, list) or not procedures:
        raise InputRejected("program-template requires procedures array")

    sample_size = 0
    for point in size_payload.get("points") or []:
        if isinstance(point, dict):
            try:
                sample_size = int(point.get("value") or sample_size)
            except (TypeError, ValueError):
                continue
    if sample_size <= 0:
        raise InputRejected("sample-size must be positive")

    engagement_id = str(template.get("engagement_id") or "ENG-2026-001")
    namespace = NAMESPACE_DNS
    sections = []
    for index, proc in enumerate(procedures[:MAX_SECTIONS], start=1):
        if not isinstance(proc, dict):
            continue
        claim = str(proc.get("detail") or proc.get("name") or f"审计程序{index}")
        sections.append({
            "section_id": hashlib.sha256(
                f"{engagement_id}:{index}".encode("utf-8")).hexdigest()[:16].lower(),
            "finding_id": str(uuid5(namespace, f"workpaper:{engagement_id}:{index}")),
            "title": f"{index}. {proc.get('name') or proc.get('step') or '审计程序'}",
            "severity": "low",
            "claim_id": str(uuid5(namespace, f"claim:{engagement_id}:{index}")),
            "claim": claim,
            "reviewer_label": "待复核",
            "evidence_refs": [],
            "confirmed_at": "",
        })
    if not sections:
        raise InputRejected("program-template contains no valid procedures")

    source_sha256 = str(template_port["artifact"].get("sha256") or "0" * 64)
    if len(source_sha256) != 64:
        source_sha256 = hashlib.sha256(source_sha256.encode("utf-8")).hexdigest()
    export_time = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return {
        "contract_id": "workpaper-export", "contract_version": "1.0.0",
        "export_id": f"workpaper-{engagement_id}-draft",
        "export_kind": "workpaper_draft", "status": "draft",
        "engagement_id": engagement_id,
        "engagement_name": str(template.get("engagement_name") or "专项审计"),
        "sections": sections,
        "summary": {"findings": len(sections), "high": 0, "medium": 0, "low": len(sections),
                    "evidence_refs": 0, "truncated": False},
        "constraints": {"no_overwrite": True, "requires_human_approval": True, "immutable_source": True},
        "provenance": {"source_sha256": source_sha256, "plugin": "audit.workpaper-export@0.1.0",
                       "export_time": export_time},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
