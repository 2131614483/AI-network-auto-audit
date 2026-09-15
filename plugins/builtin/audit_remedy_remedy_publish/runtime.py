# -*- coding: utf-8 -*-
"""audit.remedy.remedy-publish: publish the closed remedy ledger as an internal notice

Read-only remedy-stage plugin. No network, no writes outside the staging
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

PLUGIN_ID = "audit.remedy.remedy-publish"
CAPABILITY = "audit.remedy.remedy-publish"

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
    if not closed:
        raise InputRejected("remedy ledger is not closed for publishing")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "整改结果公示",
            "summary": "已完成整改销号任务已公示，接受全员监督",
            "notice": "本轮整改任务已全部销号，整改进度与成效已公示。",
            "published": True,
            "mask_fields": ["person_name", "evidence_uri"],
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
