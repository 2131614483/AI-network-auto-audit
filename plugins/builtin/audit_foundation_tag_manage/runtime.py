"""audit.foundation.tag-manage: audit tag taxonomy management.

Consumes a tag-query artifact (JSON) declaring an object type, the dimensions
to bucket by and optional rows, and produces a tag-tree (contract) with the
built-in audit taxonomy and deterministic counts.  Read-only projection.
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

PLUGIN_ID = "audit.foundation.tag-manage"
CAPABILITY = "audit.foundation.tag-manage"

# Built-in audit taxonomy: dimension -> ordered (tag, predicate) rules.
# A row is bucketed into the first matching tag of the dimension.
AMOUNT_BANDS = (
    ("小额", lambda amount: amount < 10_000),
    ("中额", lambda amount: amount < 100_000),
    ("大额", lambda amount: amount >= 100_000),
)

STATUS_TAGS = ("正常", "异常")


def _row_amount(row: dict[str, Any]) -> float | None:
    for key in ("amount", "debit_amount", "credit_amount", "金额"):
        raw = row.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _row_status(row: dict[str, Any]) -> str:
    raw = row.get("status") or row.get("flag") or ""
    text = str(raw).strip().lower()
    return "异常" if text and text not in {"正常", "normal", "ok", "pass", "valid", "0", ""} else "正常"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    port = envelope.get("tag-query")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("tag-query reference is missing")
    artifact = port["artifact"]
    read_verified_artifact(artifact, roots)
    query = read_artifact_json(artifact, roots)
    if not isinstance(query, dict):
        raise InputRejected("tag-query payload must be an object")

    object_type = str(query.get("object_type") or "ledger")
    dimensions = query.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions or not all(isinstance(d, str) for d in dimensions):
        raise InputRejected("tag-query requires a non-empty dimensions list")
    rows = query.get("rows")
    if rows is None:
        rows = []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise InputRejected("tag-query rows must be an array of objects")

    result: list[dict[str, Any]] = []
    for dimension in dimensions:
        tags: list[dict[str, Any]] = []
        if dimension in ("amount-band", "金额区间"):
            counts = {"小额": 0, "中额": 0, "大额": 0}
            for row in rows:
                amount = _row_amount(row)
                if amount is None:
                    continue
                for tag, predicate in AMOUNT_BANDS:
                    if predicate(amount):
                        counts[tag] += 1
                        break
            tags = [{"tag": tag, "count": counts[tag]} for tag in ("小额", "中额", "大额")]
        elif dimension in ("entry-status", "状态"):
            counts = {"正常": 0, "异常": 0}
            for row in rows:
                counts[_row_status(row)] += 1
            tags = [{"tag": tag, "count": counts[tag]} for tag in STATUS_TAGS]
        elif dimension in ("risk-level", "风险等级"):
            counts = {"低": 0, "中": 0, "高": 0}
            for row in rows:
                raw = str(row.get("severity") or row.get("level") or "低").strip()
                if raw in ("高", "high", "HIGH"):
                    counts["高"] += 1
                elif raw in ("中", "medium", "MEDIUM"):
                    counts["中"] += 1
                else:
                    counts["低"] += 1
            tags = [{"tag": tag, "count": counts[tag]} for tag in ("低", "中", "高")]
        else:
            raise InputRejected(f"不支持的标签维度: {dimension}")
        result.append({"dimension": dimension, "tags": tags})

    return {
        "contract_id": "tag-tree",
        "contract_version": "1.0.0",
        "object_type": object_type,
        "total_rows": len(rows),
        "dimensions": result,
    }


if __name__ == "__main__":
    from plugins.builtin._child_common import child_main

    raise SystemExit(child_main(handle))
