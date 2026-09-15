"""audit.evidence.evidence-archive: classify evidence items by type and program into an archive

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

PLUGIN_ID = "audit.evidence.evidence-archive"
CAPABILITY = "audit.evidence.evidence-archive"

def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("evidence-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("evidence-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)

    kinds = {"photo": 0, "confirm": 0, "inventory": 0, "interview": 0, "minutes": 0, "other": 0}
    rows = []
    if isinstance(payload, dict):
        for key in ("evidence", "items", "photos", "artifacts"):
            rows = payload.get(key) if isinstance(payload.get(key), list) else rows
            if rows:
                break
        if not rows and isinstance(payload.get("metadata"), dict):
            for key in ("photos", "items"):
                rows = payload["metadata"].get(key) if isinstance(payload["metadata"].get(key), list) else rows
                if rows:
                    break
        if not rows and isinstance(payload.get("artifact"), list):
            rows = payload["artifact"]
    if not isinstance(rows, list):
        rows = []
    for index, item in enumerate(rows[:10_000]):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("type") or "other").lower()
        if kind not in kinds:
            kind = "other"
        kinds[kind] += 1
    archived = []
    for index, item in enumerate(rows[:10_000]):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("type") or "other").lower()
        if kind not in kinds:
            kind = "other"
        archived.append({
            "row_ref": f"ev:{item.get('evidence_id') or item.get('id') or index + 1}",
            "kind": kind,
            "source": str(item.get("source_ref") or item.get("source") or "field"),
        })
    if not archived:
        raise InputRejected("evidence-input produced no archive rows")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026") if isinstance(payload, dict) else "2026",
        "artifact": {
            "title": "审计证据分类归档",
            "summary": f"归档 {len(archived)} 条，按类型分类：{kinds}",
            "archived": archived,
            "kinds": kinds,
        },
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
