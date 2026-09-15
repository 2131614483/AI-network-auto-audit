"""audit.field.confirm-letter: model the confirmation-of-balance workflow
from given counterparty confirmations.

Consumes a confirm payload (artifact-ref JSON) and emits a workflow whose
nodes are the confirmation letters and whose edges follow each letter's
stated reply status. Reply statuses are taken from the input; nothing is
inferred. Read-only.
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

PLUGIN_ID = "audit.field.confirm-letter"
CAPABILITY = "audit.field.confirm-letter"
MAX_LETTERS = 20_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("confirm-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("confirm-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    letters = payload.get("confirmations")
    if not isinstance(letters, list):
        raise InputRejected("confirm-input requires confirmations array")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    replied = 0
    for index, letter in enumerate(letters[:MAX_LETTERS], start=1):
        if not isinstance(letter, dict):
            continue
        node_id = f"confirm-{index:04d}"
        status = str(letter.get("reply_status") or "pending")
        if status == "replied":
            replied += 1
        nodes.append({
            "id": node_id,
            "name": str(letter.get("counterparty") or f"对方{index}"),
            "props": {
                "amount": letter.get("amount"),
                "currency": str(letter.get("currency") or "CNY"),
                "reply_status": status,
                "replied_at": str(letter.get("replied_at") or ""),
            },
        })
        edges.append({"id": f"e-{index:04d}", "source": node_id, "target": node_id, "label": status})
    if not nodes:
        raise InputRejected("confirm-input contains no confirmations")

    total = len(nodes)
    return {
        "workflow_id": f"confirm-{payload.get('period') or 'period'}",
        "tenant_id": str(payload.get("tenant_id") or "local-dev"),
        "key": "confirm-letter", "version": "1.0.0",
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        "nodes": nodes, "edges": edges,
        "checksum": {"total": total, "replied": replied,
                     "reply_rate": round(replied / total, 4) if total else 0.0},
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
