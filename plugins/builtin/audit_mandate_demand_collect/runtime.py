"""audit.mandate.demand-collect: normalise audit demands from business units
into a demand-set document.

Consumes a demand payload (artifact-ref JSON) and emits a document-content
with one demand entry per submitted request. Demands pass through as-is.
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

PLUGIN_ID = "audit.mandate.demand-collect"
CAPABILITY = "audit.mandate.demand-collect"
MAX_DEMANDS = 20_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("demand-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("demand-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    demands = payload.get("demands")
    if not isinstance(demands, list):
        raise InputRejected("demand-input requires demands array")

    entries = []
    for index, demand in enumerate(demands[:MAX_DEMANDS], start=1):
        if not isinstance(demand, dict):
            continue
        entries.append({
            "demand_id": str(demand.get("demand_id") or f"DM-{index:04d}"),
            "dept": str(demand.get("dept") or ""),
            "title": str(demand.get("title") or ""),
            "priority": str(demand.get("priority") or "medium"),
            "detail": str(demand.get("detail") or ""),
            "source_ref": str(demand.get("source_ref") or "demand-collect"),
        })
    if not entries:
        raise InputRejected("demand-input contains no demands")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "审计需求征集汇总",
            "period": str(payload.get("period") or "2026"),
            "summary": {"demand_count": len(entries),
                        "by_dept": {dept: sum(1 for e in entries if e["dept"] == dept)
                                    for dept in sorted({e["dept"] for e in entries})}},
            "demands": entries,
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
