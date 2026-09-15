"""audit.risk.internal-control-risk-map: walk the auditee's internal-control
flow and map control gaps as risk candidates.

Consumes an ic-flow document (control_steps list with control flags) and
emits ic-risk-set (anomaly-candidates) for steps lacking a control or
authorization. Read-only.
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

PLUGIN_ID = "audit.risk.internal-control-risk-map"
CAPABILITY = "audit.risk.internal-control-risk-map"
MAX_STEPS = 10_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("ic-flow")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("ic-flow reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    body = payload.get("artifact")
    steps = body.get("control_steps") if isinstance(body, dict) else payload.get("control_steps")
    if not isinstance(steps, list) or not steps:
        raise InputRejected("ic-flow requires control_steps array")

    candidates = []
    for index, step in enumerate(steps[:MAX_STEPS]):
        if not isinstance(step, dict):
            continue
        has_control = bool(step.get("control") or step.get("has_control"))
        has_approval = bool(step.get("approval") or step.get("has_approval"))
        if has_control and has_approval:
            continue
        candidates.append({
            "rule_id": f"IC-{index + 1:04d}",
            "rule_key": "control_gap",
            "severity": "high" if not has_control else "medium",
            "row_ref": f"ic-step:{step.get('step_id') or index + 1}",
            "source_ref": "internal-control-risk-map",
            "score": round(0.8 if not has_control else 0.5, 2),
            "note": f"内控步骤「{step.get('name') or step.get('step') or index + 1}」"
                    f"缺少{'控制点' if not has_control else ''}{'审批' if not has_approval else ''}",
        })
    if not candidates:
        raise InputRejected("ic-flow produced no control-gap candidates")

    return {
        "contract_id": "anomaly-candidates", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026"),
        "summary": {"candidates": len(candidates)},
        "candidates": candidates,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
