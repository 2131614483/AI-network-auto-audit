"""audit.finding.responsible-party-find: attribute issues to responsible
departments / roles.

Consumes the issue trace (finding-draft) plus a master-map payload
(dataset-validation shaped: rows carry dept_code / dept_name / owner /
account_code) and resolves each issue's responsible party by its row /
account reference.  Unmatched issues stay "待人工认定" — never invented.
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

PLUGIN_ID = "audit.finding.responsible-party-find"
CAPABILITY = "audit.finding.responsible-party-find"
MAX_ISSUES = 2_000


def _master_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    checks = payload.get("checks")
    if isinstance(checks, list):
        return [check for check in checks if isinstance(check, dict) and "dept_code" in check]
    return []


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    issue_port = envelope.get("issue-trace-input")
    master_port = envelope.get("master-map")
    if not isinstance(issue_port, dict) or not isinstance(issue_port.get("artifact"), dict):
        raise InputRejected("issue-trace-input reference is missing")
    if not isinstance(master_port, dict) or not isinstance(master_port.get("artifact"), dict):
        raise InputRejected("master-map reference is missing")
    roots = allowed_roots()
    read_verified_artifact(issue_port["artifact"], roots)
    read_verified_artifact(master_port["artifact"], roots)
    issues = read_artifact_json(issue_port["artifact"], roots)
    master = read_artifact_json(master_port["artifact"], roots)
    findings = issues.get("findings")
    rows = _master_rows(master)
    if not isinstance(findings, list):
        raise InputRejected("issue-trace-input is not a finding-draft payload")

    matched = 0
    for finding in findings:
        if len(rows) == 0:
            break
        row_ref = str(finding.get("row_ref") or "")
        source_ref = str(finding.get("source_ref") or "")
        party: dict[str, Any] | None = None
        for row in rows:
            if row_ref and row_ref in (
                str(row.get("entry_id") or ""),
                str(row.get("account_code") or ""),
                str(row.get("row_ref") or ""),
            ):
                party = {
                    "dept_code": row.get("dept_code"),
                    "dept_name": row.get("dept_name"),
                    "role": row.get("role") or "经办",
                    "owner": row.get("owner"),
                }
                break
        if party is None and source_ref and rows:
            for row in rows:
                if str(row.get("source_ref") or "") == source_ref:
                    party = {
                        "dept_code": row.get("dept_code"),
                        "dept_name": row.get("dept_name"),
                        "role": row.get("role") or "经办",
                        "owner": row.get("owner"),
                    }
                    break
        if party is not None:
            finding["responsible_party"] = party
            finding["party_basis"] = "master_map"
            matched += 1
        else:
            finding["responsible_party"] = None
            finding["party_basis"] = "pending_human"

    summary = dict(issues.get("summary") or {})
    summary["responsible_matched"] = matched
    summary["pending_human"] = len(findings) - matched
    return {
        "contract_id": "finding-draft",
        "contract_version": "1.0.0",
        "source_ref": issues.get("source_ref"),
        "summary": summary,
        "findings": findings,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
