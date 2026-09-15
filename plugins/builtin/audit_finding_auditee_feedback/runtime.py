"""audit.finding.auditee-feedback: open the auditee confirmation round.

Each graded issue receives a feedback record with a default response window
(10 working days) and an explicit pending status — the runtime never invents
an auditee opinion; confirmation is left to the auditee / human channel.
Read-only.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.finding.auditee-feedback"
CAPABILITY = "audit.finding.auditee-feedback"
MAX_ISSUES = 2_000
DEFAULT_RESPONSE_DAYS = 10


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("graded-issue")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("graded-issue reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    payload = read_artifact_json(artifact, roots)
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise InputRejected("graded-issue is not a finding-draft payload")

    today = date.today()
    deadline = today + timedelta(days=DEFAULT_RESPONSE_DAYS)
    for finding in findings:
        if len(findings) > MAX_ISSUES:
            break
        finding["feedback"] = {
            "status": "待反馈",
            "requested_at": today.isoformat(),
            "response_deadline": deadline.isoformat(),
            "auditee_opinion": None,
            "agreed": None,
        }

    summary = dict(payload.get("summary") or {})
    summary["feedback_pending"] = len(findings)
    return {
        "contract_id": "finding-draft",
        "contract_version": "1.0.0",
        "source_ref": payload.get("source_ref"),
        "summary": summary,
        "findings": findings,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
