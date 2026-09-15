"""audit.finding.issue-grade: grade issues by quantified amount + severity.

Merges the issue-amount finding-draft (primary) with the responsible-party
finding-draft, grades each issue (重大 / 重要 / 一般) on amount and severity
thresholds and emits a graded-issue finding-draft.  Read-only.
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

PLUGIN_ID = "audit.finding.issue-grade"
CAPABILITY = "audit.finding.issue-grade"
MAX_ISSUES = 2_000
MAJOR_AMOUNT = 1_000_000.0
IMPORTANT_AMOUNT = 100_000.0


def _grade(amount: float, severity: str) -> str:
    severity = str(severity or "low").lower()
    if amount >= MAJOR_AMOUNT or severity == "high":
        return "重大"
    if amount >= IMPORTANT_AMOUNT or severity == "medium":
        return "重要"
    return "一般"


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    amount_port = envelope.get("issue-amount")
    party_port = envelope.get("responsible-party")
    if not isinstance(amount_port, dict) or not isinstance(amount_port.get("artifact"), dict):
        raise InputRejected("issue-amount reference is missing")
    if not isinstance(party_port, dict) or not isinstance(party_port.get("artifact"), dict):
        raise InputRejected("responsible-party reference is missing")
    roots = allowed_roots()
    read_verified_artifact(amount_port["artifact"], roots)
    read_verified_artifact(party_port["artifact"], roots)
    amount_payload = read_artifact_json(amount_port["artifact"], roots)
    party_payload = read_artifact_json(party_port["artifact"], roots)
    findings = amount_payload.get("findings")
    party_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(party_payload, dict) and isinstance(party_payload.get("findings"), list):
        for item in party_payload["findings"]:
            if isinstance(item, dict) and item.get("issue_id"):
                party_by_id[str(item["issue_id"])] = item
    if not isinstance(findings, list):
        raise InputRejected("issue-amount is not a finding-draft payload")

    by_grade: dict[str, int] = {}
    for finding in findings:
        if len(by_grade) >= MAX_ISSUES:
            break
        amount = float(finding.get("amount") or 0.0)
        grade = _grade(amount, finding.get("severity"))
        finding["grade"] = grade
        by_grade[grade] = by_grade.get(grade, 0) + 1
        party = party_by_id.get(str(finding.get("issue_id") or ""))
        if party is not None:
            finding["responsible_party"] = party.get("responsible_party")
            finding["party_basis"] = party.get("party_basis")

    summary = dict(amount_payload.get("summary") or {})
    summary["by_grade"] = by_grade
    return {
        "contract_id": "finding-draft",
        "contract_version": "1.0.0",
        "source_ref": amount_payload.get("source_ref"),
        "summary": summary,
        "findings": findings,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
