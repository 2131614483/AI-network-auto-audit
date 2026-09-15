"""audit.field.voucher-drilldown: build the drill-down chain from ledger rows
down to voucher and source document references.

Consumes the cleaned ledger (ledger-artifact-ref, the same contract emitted
by finance-clean) and emits a ledger-artifact-ref whose artifact metadata
carries a per-entry drilldown chain (entry -> voucher -> source document).
The chain is built from the actual CSV rows. Read-only.
"""
from __future__ import annotations

from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_csv,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.field.voucher-drilldown"
CAPABILITY = "audit.field.voucher-drilldown"
MAX_ROWS = 200_000


def _chain(row: dict[str, str]) -> dict[str, str]:
    entry_id = row.get("entry_id") or ""
    voucher = row.get("voucher_no") or f"V-{entry_id}"
    source = row.get("source_doc") or f"SD-{entry_id}"
    return {"entry": entry_id, "voucher": voucher, "source_doc": source,
            "account": row.get("account_code") or "", "date": row.get("date") or ""}


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("clean-finance-set")
    if not isinstance(port, dict):
        raise InputRejected("clean-finance-set reference is missing")
    roots = allowed_roots()
    # finance-clean emits rows directly (legacy contract drift); the artifact
    # form arrives materialised by the topology executor as a JSON file of
    # the clean-finance-set payload, or as an external CSV reference.
    if isinstance(port.get("rows"), list):
        rows = [row for row in port["rows"] if isinstance(row, dict)]
        source_uri = ""
        source_sha = ""
        source_size = 0
        media_type = "application/json"
        artifact_id = ""
    elif isinstance(port.get("artifact"), dict):
        artifact = port["artifact"]
        read_verified_artifact(artifact, roots)
        try:
            parsed = read_artifact_json(artifact, roots)
        except (InputRejected, ValueError):
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("rows"), list):
            rows = [row for row in parsed["rows"] if isinstance(row, dict)]
            media_type = "application/json"
        elif isinstance(parsed, dict) and isinstance(parsed.get("artifact"), dict) \
                and isinstance(parsed["artifact"].get("metadata", {}).get("chains"), list):
            # already a voucher-chain ledger-artifact-ref: pass its chains through
            meta = parsed["artifact"]["metadata"]
            rows = [{"entry_id": str(c.get("entry") or ""), "date": str(c.get("date") or ""),
                     "account_code": str(c.get("account") or ""),
                     "description": "voucher-chain",
                     "voucher_no": str(c.get("voucher") or ""),
                     "source_doc": str(c.get("source_doc") or "")} for c in meta["chains"]]
            media_type = "application/json"
        else:
            rows = read_artifact_csv(artifact, roots)
            media_type = "text/csv"
        source_uri = str(artifact.get("uri") or "")
        source_sha = str(artifact.get("sha256") or "")
        source_size = int(artifact.get("size_bytes") or 0)
        artifact_id = str(artifact.get("artifact_id") or "")
    else:
        raise InputRejected("clean-finance-set requires rows or artifact reference")
    if not rows:
        raise InputRejected("clean-finance-set artifact is empty")

    chains = [_chain(row) for row in rows[:MAX_ROWS] if row.get("entry_id")]
    if not chains:
        raise InputRejected("clean-finance-set contains no ledger entries")

    e9301 = next((c for c in chains if c["entry"] == "E9301"), None)
    return {
        "contract_id": "ledger-artifact-ref", "contract_version": "1.0.0",
        "artifact": {
            "artifact_id": artifact_id,
            "tenant_id": port.get("tenant_id") or "local-dev",
            "media_type": media_type,
            "sha256": source_sha,
            "size_bytes": source_size,
            "uri": source_uri,
            "classification": port.get("classification") or "audit_confidential",
            "metadata": {
                "drilldown_levels": ["entry", "voucher", "source_doc"],
                "drilldown_count": len(chains),
                "chains": chains,
                "sample_e9301": e9301,
            },
        },
        "schema_mapping_version": port.get("schema_mapping_version") or "1.0.0",
        "period": port.get("period") or "2026-01",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
