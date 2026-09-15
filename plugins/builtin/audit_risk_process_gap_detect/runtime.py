"""audit.risk.process-gap-detect: scan business process steps for breakpoints
(missing owner or approval) and emit risk candidates.

Consumes a process-input document (process_steps list) and emits
process-gap-set (anomaly-candidates) for broken steps. Read-only.
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

PLUGIN_ID = "audit.risk.process-gap-detect"
CAPABILITY = "audit.risk.process-gap-detect"
MAX_STEPS = 10_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("process-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("process-input reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    body = payload.get("artifact")
    steps = body.get("process_steps") if isinstance(body, dict) else payload.get("process_steps")
    if not isinstance(steps, list) or not steps:
        raise InputRejected("process-input requires process_steps array")

    candidates = []
    for index, step in enumerate(steps[:MAX_STEPS]):
        if not isinstance(step, dict):
            continue
        owner = str(step.get("owner") or "")
        approval = bool(step.get("approval") or step.get("has_approval"))
        broken = not owner or not approval
        if not broken:
            continue
        candidates.append({
            "rule_id": f"PROC-{index + 1:04d}",
            "rule_key": "process_gap",
            "severity": "high" if not owner else "medium",
            "row_ref": f"process:{step.get('step_id') or index + 1}",
            "source_ref": "process-gap-detect",
            "score": round(0.85 if not owner else 0.55, 2),
            "note": f"流程步骤「{step.get('name') or step.get('step') or index + 1}」"
                    f"缺少{'责任人' if not owner else ''}{'审批' if not approval else ''}",
        })
    if not candidates:
        raise InputRejected("process-input produced no process-gap candidates")

    return {
        "contract_id": "anomaly-candidates", "contract_version": "1.0.0",
        "period": str(payload.get("period") or "2026"),
        "summary": {"candidates": len(candidates)},
        "candidates": candidates,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
