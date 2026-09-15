"""audit.field.suspicion-flag: flag field findings into suspicion candidates.

Consumes a field-finding payload (artifact-ref JSON) and normalises each
entry into an anomaly-candidates candidate for downstream suspicion-merge.
Field notes are passed through as-is; nothing is inferred beyond the note.
Read-only.
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

PLUGIN_ID = "audit.field.suspicion-flag"
CAPABILITY = "audit.field.suspicion-flag"
MAX_CANDIDATES = 2_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("field-finding-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("field-finding-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    raw_findings = payload.get("field_findings")
    if not isinstance(raw_findings, list):
        raise InputRejected("field-finding-input requires field_findings array")

    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(raw_findings, start=1):
        if not isinstance(item, dict) or len(candidates) >= MAX_CANDIDATES:
            continue
        candidates.append({
            "rule_id": str(item.get("rule_id") or f"FIELD-{index:04d}"),
            "rule_key": str(item.get("rule_key") or "field_note"),
            "severity": str(item.get("severity") or "medium"),
            "row_ref": str(item.get("row_ref") or item.get("entry_id") or ""),
            "source_ref": str(item.get("source_ref") or "field"),
            "score": float(item.get("score") or 0.5),
            "field_note": str(item.get("note") or ""),
        })

    return {
        "contract_id": "anomaly-candidates",
        "contract_version": "1.0.0",
        "ledger_sha256": payload.get("ledger_sha256"),
        "schema_mapping_version": "1.0.0",
        "period": str(payload.get("period") or "2026-01"),
        "rule_pack_sha256": payload.get("rule_pack_sha256"),
        "summary": {"field_findings": len(raw_findings), "candidates": len(candidates)},
        "candidates": candidates,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
