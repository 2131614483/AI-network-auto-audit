"""audit.foundation.rule-engine: deterministic rule evaluation.

Evaluates a configured rule pack (operator/field/value) over data rows and
produces a rule-evaluation report: per-rule hit counts and a flattened hit
list.  Stdlib-only; read-only.  Operators: eq/ne/gt/gte/lt/lte/contains/
not_contains.
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

PLUGIN_ID = "audit.foundation.rule-engine"
CAPABILITY = "audit.foundation.rule-engine"
OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "not_contains")


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator in ("eq", "ne"):
        match = str(left).strip() == str(right).strip()
        return match if operator == "eq" else not match
    if operator in ("gt", "gte", "lt", "lte"):
        try:
            lv, rv = float(left), float(right)
        except (TypeError, ValueError):
            return False
        return {"gt": lv > rv, "gte": lv >= rv, "lt": lv < rv, "lte": lv <= rv}[operator]
    if operator in ("contains", "not_contains"):
        match = str(right).lower() in str(left).lower()
        return match if operator == "contains" else not match
    raise InputRejected(f"不支持的规则算子: {operator}")


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    port = envelope.get("rule-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("rule-input reference is missing")
    artifact = port["artifact"]
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    if not isinstance(payload, dict):
        raise InputRejected("rule-input payload must be an object")

    rules = payload.get("rules")
    rows = payload.get("rows")
    if not isinstance(rules, list) or not rules:
        raise InputRejected("rule-input requires a non-empty rules list")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise InputRejected("rule-input rows must be an array of objects")

    evaluated: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise InputRejected("each rule must be an object")
        rule_id = str(rule.get("rule_id") or "?")
        operator = str(rule.get("operator") or "eq")
        field = str(rule.get("field") or "")
        value = rule.get("value")
        if operator not in OPERATORS:
            raise InputRejected(f"不支持的规则算子: {operator}")
        hit_rows: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            actual = row.get(field)
            if _compare(actual, operator, value):
                hit_rows.append({"row_index": index, "row_key": str(row.get("entry_id") or index), "actual": actual})
        hits.extend({"rule_id": rule_id, **hit_row} for hit_row in hit_rows)
        evaluated.append({
            "rule_id": rule_id,
            "rule_key": rule.get("rule_key"),
            "field": field,
            "operator": operator,
            "value": value,
            "hits": len(hit_rows),
            "hit_rows": [h["row_key"] for h in hit_rows][:20],
        })

    total_hits = sum(rule["hits"] for rule in evaluated)
    return {
        "contract_id": "rule-evaluation",
        "contract_version": "1.0.0",
        "summary": {"rules": len(evaluated), "rows": len(rows), "hits": total_hits},
        "rules": evaluated,
        "hits": hits,
    }


if __name__ == "__main__":
    from plugins.builtin._child_common import child_main

    raise SystemExit(child_main(handle))
