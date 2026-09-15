# -*- coding: utf-8 -*-
"""audit.govern.method-distill: distill reusable audit methods from the project review

Read-only governance-layer plugin. No network, no writes outside the staging
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

PLUGIN_ID = "audit.govern.method-distill"
CAPABILITY = "audit.govern.method-distill"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("project-review-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("project-review-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("project-review-input payload must be an object")

    body = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else payload
    notes = body.get("review_notes") if isinstance(body, dict) else None
    if not isinstance(notes, list) or not notes:
        raise InputRejected("project-review-input requires review_notes")

    methods = [
        {"method_id": f"METHOD-{i + 1:03d}", "title": str(note)}
        for i, note in enumerate(notes[:20])
        if isinstance(note, str) and note
    ]
    if not methods:
        raise InputRejected("no usable review notes to distill")
    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "审计方法沉淀",
            "summary": f"沉淀可复用方法 {len(methods)} 条",
            "methods": methods,
            "distilled": True,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
