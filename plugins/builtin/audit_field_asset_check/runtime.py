"""audit.field.asset-check: compare asset ledger locations with found
locations and emit a dataset-validation result.

Consumes an asset payload (artifact-ref JSON) with per-asset book/found
locations and emits a dataset-validation whose violations carry each
discrepancy. Read-only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.field.asset-check"
CAPABILITY = "audit.field.asset-check"
MAX_ASSETS = 100_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("asset-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("asset-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise InputRejected("asset-input requires assets array")

    violations = []
    checked_rows = 0
    for asset in assets[:MAX_ASSETS]:
        if not isinstance(asset, dict):
            continue
        checked_rows += 1
        code = str(asset.get("asset_code") or "")
        book = str(asset.get("book_loc") or "")
        found = str(asset.get("found_loc") or "")
        if book != found:
            violations.append({
                "check_id": "columns",
                "message": f"资产账实不符 asset_code={code} 账面位置 {book} 实盘位置 {found}",
            })
    if checked_rows == 0:
        raise InputRejected("asset-input contains no assets")

    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "summary": {"valid": not violations, "checked_columns": 2, "checked_rows": checked_rows,
                    "missing_values": 0},
        "checks": [{"check_id": "columns", "status": "fail" if violations else "pass",
                    "message": f"资产账实不符 {len(violations)} 项"}],
        "violations": violations,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
