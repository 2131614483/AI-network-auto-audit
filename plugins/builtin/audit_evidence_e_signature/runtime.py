"""audit.evidence.e-signature: apply e-seal to review-approved workpapers

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

PLUGIN_ID = "audit.evidence.e-signature"
CAPABILITY = "audit.evidence.e-signature"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("review-status")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("review-status reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)

    body = payload.get("artifact") if isinstance(payload, dict) else None
    rows = body.get("review_status") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        rows = payload.get("review_status") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise InputRejected("review-status requires review_status array")

    signed = []
    for index, item in enumerate(rows[:5_000]):
        if not isinstance(item, dict):
            continue
        approved = bool(item.get("approved"))
        signed.append({
            "workpaper_id": str(item.get("workpaper_id") or f"WP-{index + 1:04d}"),
            "signed": approved,
            "seal_id": f"SEAL-2026-{index + 1:04d}" if approved else "",
            "stamp": "已加盖电子签章" if approved else "未通过复核，不予签章",
        })
    if not signed:
        raise InputRejected("review-status contains no signable items")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026") if isinstance(payload, dict) else "2026",
        "artifact": {
            "title": "电子签章",
            "summary": f"签章 {len(signed)} 份底稿（通过复核 {sum(1 for s in signed if s['signed'])}）",
            "signed_workpapers": signed,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
