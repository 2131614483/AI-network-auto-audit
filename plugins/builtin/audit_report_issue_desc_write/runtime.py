"""audit.report.issue-desc-write: compose issue descriptions for the report.

Consumes the final issue set (finding-draft contract), produces a report-draft
with per-issue standardized descriptions (问题描述/金额/责任/定性分级/整改建议 sections)
and a report skeleton.  Read-only text composition.
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

PLUGIN_ID = "audit.report.issue-desc-write"
CAPABILITY = "audit.report.issue-desc-write"
MAX_ISSUES = 2_000


def _compose_description(finding: dict[str, Any]) -> str:
    category = finding.get("category", "其他")
    rule_key = finding.get("rule_key", "other")
    row_ref = finding.get("row_ref", "-")
    amount = finding.get("amount", 0.0)
    amount_text = f"{amount:,.2f} 元" if amount else "金额待核实"
    return (
        f"经核查，发现「{category}」类问题（规则 {rule_key}，涉及记录 {row_ref}，涉及金额 {amount_text}）。"
        f"该问题需按《审计整改管理办法》落实整改并复核销号。"
    )


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("final-issue-set")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("final-issue-set reference is missing")
    artifact = port["artifact"]
    roots = allowed_roots()
    read_verified_artifact(artifact, roots)
    issues = read_artifact_json(artifact, roots)
    findings = issues.get("findings")
    if not isinstance(findings, list):
        raise InputRejected("final-issue-set is not a finding-draft payload")

    rendered: list[dict[str, Any]] = []
    total = 0.0
    by_category: dict[str, int] = {}
    for index, finding in enumerate(findings[:MAX_ISSUES], start=1):
        category = finding.get("category", "其他")
        amount = float(finding.get("amount") or 0.0)
        total += amount
        by_category[category] = by_category.get(category, 0) + 1
        rendered.append(
            {
                "issue_id": finding.get("issue_id", f"ISSUE-{index:04d}"),
                "category": category,
                "severity": finding.get("severity", "low"),
                "amount": round(amount, 2),
                "description": _compose_description(finding),
                "recommended_action": finding.get("recommended_action"),
            }
        )

    return {
        "contract_id": "report-draft",
        "contract_version": "1.0.0",
        "source_ref": issues.get("source_ref"),
        "sections": ["审计概况", "审计范围与方法", "主要问题", "审计建议", "整改要求", "审计结论"],
        "summary": {"issue_count": len(rendered), "by_category": by_category, "quantified_total": round(total, 2)},
        "issues": rendered,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
