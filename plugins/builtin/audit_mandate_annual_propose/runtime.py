"""audit.mandate.annual-propose: turn aligned demands into annual
proposals.

Consumes an aligned-demand (document-content) and emits a proposal-set with
one submitted proposal per aligned demand. Only aligned demands become
proposals; no amounts are invented. Read-only.
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

PLUGIN_ID = "audit.mandate.annual-propose"
CAPABILITY = "audit.mandate.annual-propose"
MAX_PROPOSALS = 10_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("aligned-demand")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("aligned-demand reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    artifact_body = payload.get("artifact")
    demands = artifact_body.get("demands") if isinstance(artifact_body, dict) else payload.get("demands")
    if not isinstance(demands, list):
        raise InputRejected("aligned-demand requires demands array")

    proposals = []
    for index, demand in enumerate(demands[:MAX_PROPOSALS], start=1):
        if not isinstance(demand, dict):
            continue
        if demand.get("alignment") == "待人工确认":
            continue
        proposals.append({
            "proposal_id": f"PR-{index:04d}",
            "title": str(demand.get("title") or ""),
            "source_demand": str(demand.get("demand_id") or ""),
            "dept": str(demand.get("dept") or ""),
            "priority": str(demand.get("priority") or "medium"),
            "status": "submitted",
            "basis": str(demand.get("alignment_basis") or ""),
        })
    if not proposals:
        raise InputRejected("aligned-demand contains no aligned proposals")

    return {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "年度立项申报清单",
            "summary": {"proposal_count": len(proposals),
                        "by_priority": {p: sum(1 for x in proposals if x["priority"] == p)
                                        for p in sorted({x["priority"] for x in proposals})}},
            "proposals": proposals,
        },
        "language": "zh-CN",
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
