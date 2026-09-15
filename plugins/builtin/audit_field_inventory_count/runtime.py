"""audit.field.inventory-count: compare book vs counted quantities and emit
a dataset-validation result.

Consumes an inventory payload (artifact-ref JSON) with per-SKU book/count
quantities and emits a dataset-validation whose violations carry each
difference (check_id enum-compliant). Read-only.
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

PLUGIN_ID = "audit.field.inventory-count"
CAPABILITY = "audit.field.inventory-count"
MAX_ITEMS = 100_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("inventory-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("inventory-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    items = payload.get("items")
    if not isinstance(items, list):
        raise InputRejected("inventory-input requires items array")

    violations = []
    diff_items = []
    checked_rows = 0
    for item in items[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        checked_rows += 1
        sku = str(item.get("sku") or "")
        try:
            book = float(item.get("book_qty") or 0)
            count = float(item.get("count_qty") or 0)
        except (TypeError, ValueError):
            raise InputRejected(f"inventory-input has invalid quantity for sku={sku}")
        diff = round(book - count, 4)
        if diff != 0:
            diff_items.append({"sku": sku, "book_qty": book, "count_qty": count, "diff": diff})
            violations.append({
                "check_id": "columns",
                "message": f"盘点差异 sku={sku} 账面 {book:g} 实盘 {count:g} 差异 {diff:g}",
            })
    if checked_rows == 0:
        raise InputRejected("inventory-input contains no items")

    return {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "snapshot_sha256": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "summary": {"valid": not violations, "checked_columns": 2, "checked_rows": checked_rows,
                    "missing_values": 0},
        "checks": [{"check_id": "columns", "status": "fail" if violations else "pass",
                    "message": f"账实差异 {len(diff_items)} 项"}],
        "violations": violations,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
