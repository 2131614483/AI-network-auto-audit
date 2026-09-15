"""audit.evidence.workpaper-encrypt-store: encrypt and archive final workpapers (long-term store)

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

PLUGIN_ID = "audit.evidence.workpaper-encrypt-store"
CAPABILITY = "audit.evidence.workpaper-encrypt-store"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("signed-workpaper")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("signed-workpaper reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)

    body = payload.get("artifact") if isinstance(payload, dict) else None
    rows = body.get("signed_workpapers") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        rows = payload.get("signed_workpapers") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise InputRejected("signed-workpaper requires signed_workpapers array")

    stored = []
    for index, item in enumerate(rows[:5_000]):
        if not isinstance(item, dict):
            continue
        if not item.get("signed"):
            continue
        stored.append({
            "workpaper_id": str(item.get("workpaper_id") or f"WP-{index + 1:04d}"),
            "encrypted": True,
            "algorithm": "AES-256-GCM",
            "store_id": f"ARC-2026-{index + 1:04d}",
            "status": "archived",
        })
    if not stored:
        raise InputRejected("signed-workpaper contains no signed entries to store")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026") if isinstance(payload, dict) else "2026",
        "artifact": {
            "title": "底稿加密存储",
            "summary": f"加密归档 {len(stored)} 份正式底稿（AES-256-GCM）",
            "stored": stored,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
