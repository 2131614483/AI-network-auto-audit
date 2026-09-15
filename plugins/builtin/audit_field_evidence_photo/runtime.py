"""audit.field.evidence-photo: register field photos as an evidence
artifact reference.

Consumes a photo payload (artifact-ref JSON) and emits a single artifact-ref
aggregating the registered photos (count + metadata). Photos are passed
through as-is; no image content is inferred. Read-only.
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

PLUGIN_ID = "audit.field.evidence-photo"
CAPABILITY = "audit.field.evidence-photo"
MAX_PHOTOS = 50_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("photo-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("photo-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    photos = payload.get("photos")
    if not isinstance(photos, list):
        raise InputRejected("photo-input requires photos array")
    if not photos:
        raise InputRejected("photo-input contains no photos")

    registered = []
    for index, photo in enumerate(photos[:MAX_PHOTOS], start=1):
        if not isinstance(photo, dict):
            continue
        registered.append({
            "photo_id": str(photo.get("photo_id") or f"PHOTO-{index:04d}"),
            "row_ref": str(photo.get("row_ref") or ""),
            "ts": str(photo.get("ts") or ""),
            "media_type": str(photo.get("media_type") or "image/png"),
            "sha256": str(photo.get("sha256") or ""),
            "note": str(photo.get("note") or ""),
        })
    if not registered:
        raise InputRejected("photo-input contains no valid photos")

    return {
        "contract_id": "artifact-ref", "contract_version": "1.0.0",
        "artifact_id": str(payload.get("evidence_bundle_id") or f"evidence-photo-{payload.get('period') or 'bundle'}"),
        "tenant_id": str(payload.get("tenant_id") or "local-dev"),
        "media_type": "application/json",
        "sha256": str(artifact.get("sha256") or ""),
        "size_bytes": int(artifact.get("size_bytes") or 0),
        "uri": str(artifact.get("uri") or ""),
        "classification": "audit_confidential",
        "metadata": {"photo_count": len(registered), "photos": registered},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
