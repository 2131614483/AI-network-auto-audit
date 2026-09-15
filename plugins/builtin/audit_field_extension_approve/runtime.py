"""audit.field.extension-approve: decide on an extension request.

Consumes an extension-request payload (document-content JSON) and emits a
workflow with the decision: requests up to 3 days are auto-approved, longer
ones are routed to human review (pending_human). The decision is rule-based
and deterministic; no approver is invented. Read-only.
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

PLUGIN_ID = "audit.field.extension-approve"
CAPABILITY = "audit.field.extension-approve"
MAX_DAYS_AUTO_APPROVE = 3


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("extension-request")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("extension-request reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if isinstance(payload.get("artifact"), dict) and isinstance(payload["artifact"].get("text"), str):
        payload = payload["artifact"]

    request_id = str(payload.get("request_id") or "unknown")
    try:
        days = int(payload.get("days") or 0)
    except (TypeError, ValueError):
        raise InputRejected("extension-request requires integer days")
    if days < 0:
        raise InputRejected("extension-request days must be non-negative")

    decision = "approved" if days <= MAX_DAYS_AUTO_APPROVE else "human_review"
    nodes = [
        {"id": "ext-request", "name": "延期申请", "props": {
            "request_id": request_id, "reason": str(payload.get("reason") or ""),
            "days": days, "due_date": str(payload.get("due_date") or ""),
            "applicant": str(payload.get("applicant") or "")}},
        {"id": "ext-decision", "name": "决策节点", "props": {
            "decision": decision, "rule": f"days<={MAX_DAYS_AUTO_APPROVE}",
            "pending_human": decision == "human_review", "approver": ""}},
    ]
    return {
        "workflow_id": f"extension-{request_id}",
        "tenant_id": str(payload.get("tenant_id") or "local-dev"),
        "key": "extension-approve", "version": "1.0.0",
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        "nodes": nodes,
        "edges": [{"id": "e1", "source": "ext-request", "target": "ext-decision"}],
        "checksum": {"decision": decision, "days": days},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
