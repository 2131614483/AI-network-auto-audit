"""Unit tests for network batch 3: finding-stage expansion
(violation-clause-match / responsible-party-find / issue-grade /
auditee-feedback / issue-final-review) + field suspicion-flag."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput
from plugins.builtin.audit_field_suspicion_flag import runtime as suspicion_flag
from plugins.builtin.audit_finding_auditee_feedback import runtime as auditee_feedback
from plugins.builtin.audit_finding_issue_final_review import runtime as issue_final_review
from plugins.builtin.audit_finding_issue_grade import runtime as issue_grade
from plugins.builtin.audit_finding_responsible_party_find import runtime as responsible_party_find
from plugins.builtin.audit_finding_violation_clause_match import runtime as violation_clause

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / ".data" / "audit-sim"


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path) -> None:
    os.environ["AUDIT_PLUGIN_READ_ROOTS"] = json.dumps([str(SIM), str(SIM.parent), str(tmp_path)])


def _write(tmp_path: Path, name: str, data) -> ArtifactInput:
    path = tmp_path / name
    if isinstance(data, bytes):
        path.write_bytes(data)
        media_type = "application/octet-stream"
    else:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        media_type = "application/json"
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type=media_type, sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_confidential",
    )


def _call(runtime, plugin_id: str, capability: str, ports: dict[str, ArtifactInput]) -> dict:
    envelope = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": plugin_id,
        "capability": capability,
        "trace_id": str(uuid4()),
    }
    for port_name, artifact in ports.items():
        envelope[port_name] = {
            "contract_id": "artifact-ref", "contract_version": "1.0.0",
            "artifact": {"uri": artifact.uri, "sha256": artifact.sha256, "size_bytes": artifact.size_bytes},
        }
    return runtime.handle(envelope)


def _finding(issue_id: str, category: str, severity: str, row_ref: str, amount: float = 0.0) -> dict:
    finding = {
        "issue_id": issue_id, "category": category, "rule_key": "outlier_amount",
        "severity": severity, "row_ref": row_ref, "source_ref": "R1",
        "description": "异常", "recommended_action": "核查", "score": 0.8,
    }
    if amount:
        finding.update({"amount": amount, "currency": "CNY", "amount_basis": "ledger_row_crossref"})
    return finding


def _draft(findings: list[dict]) -> dict:
    return {
        "contract_id": "finding-draft", "contract_version": "1.0.0",
        "source_ref": "R1", "summary": {"issue_count": len(findings)}, "findings": findings,
    }


# ---------- finding: violation-clause-match ----------

def test_violation_clause_maps_categories(tmp_path: Path) -> None:
    findings = [_finding("I1", "合规", "high", "E9001"), _finding("I2", "财务", "medium", "E9204"),
                _finding("I3", "舞弊特征", "high", "E9301")]
    result = _call(violation_clause, "audit.finding.violation-clause-match",
                   "audit.finding.violation-clause-match",
                   {"issue-type-set": _write(tmp_path, "types.json", _draft(findings))})
    assert result["contract_id"] == "finding-draft"
    assert result["summary"]["clause_matched"] == 3
    by_id = {f["issue_id"]: f for f in result["findings"]}
    assert by_id["I1"]["clause_id"] == "CL-REG-01"
    assert by_id["I2"]["clause_id"] == "CL-FIN-02"
    assert by_id["I3"]["clause_id"] == "CL-FRD-04"
    assert by_id["I3"]["regulation"] == "反舞弊管理制度"


# ---------- finding: responsible-party-find ----------

def test_responsible_party_resolves_from_master_map(tmp_path: Path) -> None:
    findings = [_finding("I1", "合规", "high", "E9301"), _finding("I2", "财务", "medium", "E9999")]
    master = {
        "contract_id": "dataset-validation", "contract_version": "1.0.0",
        "checks": [], "violations": [], "summary": {},
        "rows": [
            {"dept_code": "FIN", "dept_name": "财务部", "owner": "张会计", "role": "制单", "entry_id": "E9301"},
            {"dept_code": "PUR", "dept_name": "采购部", "owner": "李采购", "role": "经办", "entry_id": "E9201"},
        ],
    }
    result = _call(responsible_party_find, "audit.finding.responsible-party-find",
                   "audit.finding.responsible-party-find",
                   {"issue-trace-input": _write(tmp_path, "trace.json", _draft(findings)),
                    "master-map": _write(tmp_path, "master.json", master)})
    assert result["summary"]["responsible_matched"] == 1
    assert result["summary"]["pending_human"] == 1
    by_id = {f["issue_id"]: f for f in result["findings"]}
    assert by_id["I1"]["responsible_party"]["dept_name"] == "财务部"
    assert by_id["I1"]["party_basis"] == "master_map"
    assert by_id["I2"]["responsible_party"] is None
    assert by_id["I2"]["party_basis"] == "pending_human"


# ---------- finding: issue-grade ----------

def test_issue_grade_by_amount_and_severity(tmp_path: Path) -> None:
    amounts = [_finding("I1", "合规", "high", "E9301", 1500000),
               _finding("I2", "财务", "medium", "E9204", 50000),
               _finding("I3", "内控缺陷", "low", "E9001", 1000)]
    parties = [_finding("I1", "合规", "high", "E9301"),
               _finding("I2", "财务", "medium", "E9204")]
    parties[0]["responsible_party"] = {"dept_name": "财务部"}
    parties[0]["party_basis"] = "master_map"
    result = _call(issue_grade, "audit.finding.issue-grade", "audit.finding.issue-grade",
                   {"issue-amount": _write(tmp_path, "amounts.json", _draft(amounts)),
                    "responsible-party": _write(tmp_path, "parties.json", _draft(parties))})
    by_id = {f["issue_id"]: f for f in result["findings"]}
    assert by_id["I1"]["grade"] == "重大"
    assert by_id["I2"]["grade"] == "重要"
    assert by_id["I3"]["grade"] == "一般"
    assert result["summary"]["by_grade"] == {"重大": 1, "重要": 1, "一般": 1}
    assert by_id["I1"]["responsible_party"]["dept_name"] == "财务部"


# ---------- finding: auditee-feedback ----------

def test_auditee_feedback_opens_pending_round(tmp_path: Path) -> None:
    findings = [_finding("I1", "合规", "high", "E9001"), _finding("I2", "财务", "medium", "E9204")]
    result = _call(auditee_feedback, "audit.finding.auditee-feedback", "audit.finding.auditee-feedback",
                   {"graded-issue": _write(tmp_path, "graded.json", _draft(findings))})
    assert result["summary"]["feedback_pending"] == 2
    for finding in result["findings"]:
        feedback = finding["feedback"]
        assert feedback["status"] == "待反馈"
        assert feedback["auditee_opinion"] is None
        assert feedback["response_deadline"] > feedback["requested_at"]


# ---------- finding: issue-final-review ----------

def test_issue_final_review_decisions(tmp_path: Path) -> None:
    agreed = _finding("I1", "合规", "high", "E9001")
    agreed["feedback"] = {"status": "已反馈", "agreed": True}
    disputed = _finding("I2", "财务", "medium", "E9204")
    disputed["feedback"] = {"status": "已反馈", "agreed": False}
    silent = _finding("I3", "舞弊特征", "high", "E9301")
    silent["feedback"] = {"status": "待反馈", "agreed": None}
    result = _call(issue_final_review, "audit.finding.issue-final-review",
                   "audit.finding.issue-final-review",
                   {"feedback-set": _write(tmp_path, "feedback.json", _draft([agreed, disputed, silent]))})
    by_id = {f["issue_id"]: f for f in result["findings"]}
    assert by_id["I1"]["final_decision"] == "确认定案"
    assert by_id["I2"]["final_decision"] == "复核中"
    assert by_id["I3"]["final_decision"] == "待反馈"
    assert result["summary"]["by_final_decision"] == {"确认定案": 1, "复核中": 1, "待反馈": 1}


# ---------- field: suspicion-flag ----------

def test_suspicion_flag_normalises_field_findings(tmp_path: Path) -> None:
    payload = {
        "period": "2026-01", "ledger_sha256": "e" * 64, "rule_pack_sha256": "f" * 64,
        "field_findings": [
            {"rule_id": "FIELD-1", "rule_key": "voucher_gap", "severity": "high", "row_ref": "E9301",
             "source_ref": "field", "score": 0.9, "note": "现场发现凭证缺失"},
            {"rule_id": "FIELD-2", "rule_key": "sign_mismatch", "severity": "medium", "row_ref": "E9201",
             "source_ref": "field", "score": 0.7, "note": "签字不一致"},
        ],
    }
    result = _call(suspicion_flag, "audit.field.suspicion-flag", "audit.field.suspicion-flag",
                   {"field-finding-input": _write(tmp_path, "field.json", payload)})
    assert result["contract_id"] == "anomaly-candidates"
    assert result["summary"]["candidates"] == 2
    assert result["candidates"][0]["field_note"] == "现场发现凭证缺失"
    assert result["candidates"][1]["row_ref"] == "E9201"
