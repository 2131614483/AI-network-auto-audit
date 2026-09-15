"""audit.finding.issue-amount-compute: quantify draft issues against the ledger.

Cross-references each draft finding's evidence rows with the clean finance-set
to compute the involved amount (sum of row amounts), producing a finding-draft
payload enriched with amount/currency/amount_basis.  Read-only quantification.
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

PLUGIN_ID = "audit.finding.issue-amount-compute"
CAPABILITY = "audit.finding.issue-amount-compute"
MAX_ISSUES = 2_000


def _resolve_row_amount(clean_rows: list[dict[str, Any]], row_ref: object) -> float:
    key = str(row_ref)
    if not key:
        return 0.0
    for row in clean_rows:
        if str(row.get("source_row")) == key:
            debit = row.get("debit_amount")
            credit = row.get("credit_amount")
            amount = max(abs(float(debit or 0.0)), abs(float(credit or 0.0)))
            if amount > 0:
                return amount
    return 0.0


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    issue_port = envelope.get("issue-verify-input")
    if not isinstance(issue_port, dict) or not isinstance(issue_port.get("artifact"), dict):
        raise InputRejected("issue-verify-input reference is missing")
    ledger_port = envelope.get("clean-finance-set")
    if not isinstance(ledger_port, dict) or not isinstance(ledger_port.get("artifact"), dict):
        raise InputRejected("clean-finance-set reference is missing")
    roots = allowed_roots()
    read_verified_artifact(issue_port["artifact"], roots)
    read_verified_artifact(ledger_port["artifact"], roots)
    issues = read_artifact_json(issue_port["artifact"], roots)
    clean = read_artifact_json(ledger_port["artifact"], roots)
    clean_rows = clean.get("rows")
    findings = issues.get("findings")
    if not isinstance(clean_rows, list) or not isinstance(findings, list):
        raise InputRejected("finding-draft or clean-finance-set payload is malformed")

    total = 0.0
    for finding in findings:
        amount = _resolve_row_amount(clean_rows, finding.get("row_ref"))
        if amount <= 0:
            # entry-level evidence: sum all rows sharing the entry_id
            entry_id = str(finding.get("row_ref") or "")
            for row in clean_rows:
                if str(row.get("entry_id")) == entry_id:
                    amount += max(abs(float(row.get("debit_amount") or 0.0)), abs(float(row.get("credit_amount") or 0.0)))
        finding["amount"] = round(amount, 2)
        finding["currency"] = "CNY"
        finding["amount_basis"] = "ledger_row_crossref" if amount > 0 else "unquantified"
        total += amount

    summary = dict(issues.get("summary") or {})
    summary["quantified_total"] = round(total, 2)
    return {
        "contract_id": "finding-draft",
        "contract_version": "1.0.0",
        "source_ref": issues.get("source_ref"),
        "summary": summary,
        "findings": findings,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
