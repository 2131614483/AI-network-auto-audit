"""audit.finding.suspicion-merge: de-duplicate and enrich suspicion candidates.

Merges an anomaly-candidates set with photo-evidence references: candidates
are deduplicated by (rule_id, row_ref), and matching photos are attached to
the merged candidates.  Read-only aggregation.
"""
from __future__ import annotations

from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.finding.suspicion-merge"
CAPABILITY = "audit.finding.suspicion-merge"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()

    suspicion_port = envelope.get("suspicion-set")
    if not isinstance(suspicion_port, dict) or not isinstance(suspicion_port.get("artifact"), dict):
        raise InputRejected("suspicion-set reference is missing")
    suspicion_artifact = suspicion_port["artifact"]
    read_verified_artifact(suspicion_artifact, roots)
    suspicion = read_artifact_json(suspicion_artifact, roots)
    if not isinstance(suspicion, dict):
        raise InputRejected("suspicion-set payload must be an object")
    candidates = suspicion.get("candidates")
    if not isinstance(candidates, list) or not all(isinstance(c, dict) for c in candidates):
        raise InputRejected("suspicion-set candidates must be an array of objects")

    photos: list[dict[str, Any]] = []
    photo_port = envelope.get("photo-evidence")
    if isinstance(photo_port, dict) and isinstance(photo_port.get("artifact"), dict):
        read_verified_artifact(photo_port["artifact"], roots)
        photo_payload = read_artifact_json(photo_port["artifact"], roots)
        if isinstance(photo_payload, dict):
            raw_photos = photo_payload.get("photos") or photo_payload.get("items")
            if isinstance(raw_photos, list):
                photos = [p for p in raw_photos if isinstance(p, dict)]

    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    duplicates_removed = 0
    photos_attached = 0
    for candidate in candidates:
        rule_id = str(candidate.get("rule_id") or "?")
        row_ref = str(candidate.get("row_ref") or "")
        key = (rule_id, row_ref)
        if key in seen:
            duplicates_removed += 1
            continue
        seen.add(key)
        merged_candidate = dict(candidate)
        photo_refs = [
            {
                "photo_id": str(photo.get("photo_id") or "?"),
                "entry_ref": str(photo.get("row_ref") or photo.get("entry_id") or ""),
            }
            for photo in photos
            if str(photo.get("row_ref") or photo.get("entry_id") or "") == row_ref
        ]
        if photo_refs:
            merged_candidate["photo_refs"] = photo_refs
            photos_attached += len(photo_refs)
        merged.append(merged_candidate)

    return {
        "contract_id": "merged-suspicion",
        "contract_version": "1.0.0",
        "ledger_sha256": suspicion.get("ledger_sha256"),
        "period": suspicion.get("period"),
        "summary": {
            "input_candidates": len(candidates),
            "merged_candidates": len(merged),
            "duplicates_removed": duplicates_removed,
            "photos_attached": photos_attached,
        },
        "candidates": merged,
    }


if __name__ == "__main__":
    from plugins.builtin._child_common import child_main

    raise SystemExit(child_main(handle))
