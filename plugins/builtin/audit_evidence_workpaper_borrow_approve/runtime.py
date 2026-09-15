"""audit.evidence.workpaper-borrow-approve: approve or deny workpaper borrow / copy requests

Read-only evidence/workpaper plugin. No network, no writes outside the
staging artifact. Fails closed on invalid input.
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

PLUGIN_ID = "audit.evidence.workpaper-borrow-approve"
CAPABILITY = "audit.evidence.workpaper-borrow-approve"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("borrow-request")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("borrow-request reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)

    body = payload.get("artifact") if isinstance(payload, dict) and isinstance(payload.get("artifact"), dict) else payload
    reqs = body.get("requests") if isinstance(body, dict) else None
    if not isinstance(reqs, list) or not reqs:
        raise InputRejected("borrow-request requires requests array")

    statuses = []
    for index, req in enumerate(reqs[:2_000]):
        if not isinstance(req, dict):
            continue
        wp_id = str(req.get("workpaper_id") or req.get("id") or f"WP-{index + 1:04d}")
        purpose = str(req.get("purpose") or "")
        statuses.append({
            "request_id": str(req.get("request_id") or f"BORROW-{index + 1:04d}"),
            "workpaper_id": wp_id,
            "requester": str(req.get("requester") or "internal-auditor"),
            "approved": bool(purpose),
            "decision": "approved" if purpose else "denied",
            "note": "借阅用途已核验" if purpose else "缺少借阅用途，驳回",
        })
    if not statuses:
        raise InputRejected("borrow-request contains no requests")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026") if isinstance(payload, dict) else "2026",
        "artifact": {
            "title": "底稿借阅审批",
            "summary": f"审批 {len(statuses)} 条借阅申请（通过 {sum(1 for s in statuses if s['approved'])}）",
            "borrow_status": statuses,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
