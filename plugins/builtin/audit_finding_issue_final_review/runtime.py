"""audit.finding.issue-final-review: final determination of issues.

Consumes the auditee feedback round and produces the final issue set with a
deterministic review decision per finding: 无异议 -> 确认定案, 有异议 ->
复核中, 未反馈 -> 待反馈 (never silently confirmed).  Read-only.
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

PLUGIN_ID = "audit.finding.issue-final-review"
CAPABILITY = "audit.finding.issue-final-review"
MAX_ISSUES = 2_000


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("feedback-set")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("feedback-set reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise InputRejected("feedback-set is not a finding-draft payload")

    by_decision: dict[str, int] = {}
    for finding in findings:
        if len(by_decision) >= MAX_ISSUES:
            break
        feedback = finding.get("feedback")
        agreed = feedback.get("agreed") if isinstance(feedback, dict) else None
        status = feedback.get("status") if isinstance(feedback, dict) else "待反馈"
        if agreed is True and status == "已反馈":
            decision = "确认定案"
        elif agreed is False:
            decision = "复核中"
        else:
            decision = "待反馈"
        finding["final_decision"] = decision
        by_decision[decision] = by_decision.get(decision, 0) + 1

    summary = dict(payload.get("summary") or {})
    summary["by_final_decision"] = by_decision
    return {
        "contract_id": "finding-draft",
        "contract_version": "1.0.0",
        "source_ref": payload.get("source_ref"),
        "summary": summary,
        "findings": findings,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
