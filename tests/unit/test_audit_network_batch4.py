"""Unit tests for network batch 4: field-stage (现场实施) expansion —
13 plugins (site-checkin-track / audit-log / confirm-letter /
cross-dept-inquiry / evidence-photo / extension-approve / interview-record /
inventory-count / meeting-minutes / progress-report / asset-check /
voucher-drilldown / workpaper-build)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput
from plugins.builtin.audit_field_asset_check import runtime as asset_check
from plugins.builtin.audit_field_audit_log import runtime as audit_log
from plugins.builtin.audit_field_confirm_letter import runtime as confirm_letter
from plugins.builtin.audit_field_cross_dept_inquiry import runtime as cross_dept
from plugins.builtin.audit_field_evidence_photo import runtime as evidence_photo
from plugins.builtin.audit_field_extension_approve import runtime as extension_approve
from plugins.builtin.audit_field_interview_record import runtime as interview_record
from plugins.builtin.audit_field_inventory_count import runtime as inventory_count
from plugins.builtin.audit_field_meeting_minutes import runtime as meeting_minutes
from plugins.builtin.audit_field_progress_report import runtime as progress_report
from plugins.builtin.audit_field_site_checkin_track import runtime as checkin_track
from plugins.builtin.audit_field_voucher_drilldown import runtime as voucher_drilldown
from plugins.builtin.audit_field_workpaper_build import runtime as workpaper_build

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


def _call(runtime, plugin_id: str, ports: dict[str, ArtifactInput]) -> dict:
    envelope = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": plugin_id,
        "capability": plugin_id,
        "trace_id": str(uuid4()),
    }
    for port_name, artifact in ports.items():
        envelope[port_name] = {
            "contract_id": "artifact-ref", "contract_version": "1.0.0",
            "artifact": {"uri": artifact.uri, "sha256": artifact.sha256, "size_bytes": artifact.size_bytes},
        }
    return runtime.handle(envelope)


# ---------- field: site-checkin-track ----------

def test_checkin_track_aggregates_daily_counts(tmp_path: Path) -> None:
    payload = {"period": "2026-01", "checkins": [
        {"staff_id": "S1", "ts": "2026-01-15T09:00:00+08:00", "location": "A"},
        {"staff_id": "S2", "ts": "2026-01-15T10:00:00+08:00", "location": "A"},
        {"staff_id": "S1", "ts": "2026-01-16T09:00:00+08:00", "location": "B"},
    ]}
    result = _call(checkin_track, "audit.field.site-checkin-track",
                   {"checkin-input": _write(tmp_path, "checkins.json", payload)})
    assert result["contract_id"] == "metric-series"
    assert result["metric"] == "checkin_count"
    assert result["points"] == [
        {"at": "2026-01-15T00:00:00+08:00", "value": 2},
        {"at": "2026-01-16T00:00:00+08:00", "value": 1},
    ]


# ---------- field: audit-log ----------

def test_audit_log_chains_events_chronologically(tmp_path: Path) -> None:
    payload = {"log_id": "L1", "tenant_id": "t1", "events": [
        {"op": "read", "node": "ledger", "ts": "2026-01-15T10:00:00Z", "actor": "a1"},
        {"op": "export", "node": "workpaper", "ts": "2026-01-15T11:00:00Z", "actor": "a1"},
        {"op": "approve", "node": "review", "ts": "2026-01-15T09:30:00Z", "actor": "a2"},
    ]}
    result = _call(audit_log, "audit.field.audit-log",
                   {"log-event": _write(tmp_path, "events.json", payload)})
    assert result["workflow_id"] == "audit-log-L1"
    assert [n["name"] for n in result["nodes"]] == ["approve", "read", "export"]
    assert len(result["edges"]) == 2
    assert result["checksum"]["event_count"] == 3


# ---------- field: confirm-letter ----------

def test_confirm_letter_tracks_reply_rate(tmp_path: Path) -> None:
    payload = {"period": "2026-01", "confirmations": [
        {"counterparty": "供应商A", "amount": 120000, "reply_status": "replied"},
        {"counterparty": "供应商B", "amount": 80000, "reply_status": "pending"},
    ]}
    result = _call(confirm_letter, "audit.field.confirm-letter",
                   {"confirm-input": _write(tmp_path, "confirm.json", payload)})
    assert result["workflow_id"] == "confirm-2026-01"
    assert result["checksum"]["total"] == 2
    assert result["checksum"]["replied"] == 1
    assert result["checksum"]["reply_rate"] == 0.5


# ---------- field: cross-dept-inquiry ----------

def test_cross_dept_inquiry_builds_request_chains(tmp_path: Path) -> None:
    payload = {"period": "2026-01", "requests": [
        {"dept": "采购部", "data_type": "合同", "purpose": "核验", "status": "delivered"},
        {"dept": "销售部", "data_type": "订单", "purpose": "抽样"},
    ]}
    result = _call(cross_dept, "audit.field.cross-dept-inquiry",
                   {"inquiry-request": _write(tmp_path, "inquiry.json", payload)})
    assert result["checksum"]["request_count"] == 2
    assert result["checksum"]["by_status"]["delivered"] == 1
    assert result["checksum"]["by_status"]["pending"] == 1
    assert len(result["nodes"]) == 4


# ---------- field: evidence-photo ----------

def test_evidence_photo_registers_photos(tmp_path: Path) -> None:
    payload = {"period": "2026-01", "photos": [
        {"photo_id": "P1", "row_ref": "E9301", "ts": "2026-01-15T10:00:00Z", "sha256": "a" * 64},
        {"photo_id": "P2", "row_ref": "E9201", "ts": "2026-01-15T11:00:00Z", "sha256": "b" * 64},
    ]}
    result = _call(evidence_photo, "audit.field.evidence-photo",
                   {"photo-input": _write(tmp_path, "photos.json", payload)})
    assert result["contract_id"] == "artifact-ref"
    assert result["metadata"]["photo_count"] == 2
    assert result["metadata"]["photos"][0]["row_ref"] == "E9301"
    assert result["classification"] == "audit_confidential"


# ---------- field: extension-approve ----------

def test_extension_approve_decides_by_days(tmp_path: Path) -> None:
    short = {"request_id": "R1", "reason": "资料补充", "days": 2, "due_date": "2026-02-01", "applicant": "张三"}
    long = {"request_id": "R2", "reason": "函证周期长", "days": 7, "due_date": "2026-02-06", "applicant": "李四"}
    r1 = _call(extension_approve, "audit.field.extension-approve",
               {"extension-request": _write(tmp_path, "ext1.json", short)})
    r2 = _call(extension_approve, "audit.field.extension-approve",
               {"extension-request": _write(tmp_path, "ext2.json", long)})
    assert r1["checksum"]["decision"] == "approved"
    assert r2["checksum"]["decision"] == "human_review"
    assert r2["nodes"][1]["props"]["pending_human"] is True
    assert r2["nodes"][1]["props"]["approver"] == ""


# ---------- field: interview-record ----------

def test_interview_record_keeps_answered_points(tmp_path: Path) -> None:
    payload = {"period": "2026-01", "interviews": [
        {"person": "王采购", "role": "经办", "date": "2026-01-14", "qa": [
            {"q": "审批流程？", "a": "需要两级审批"},
            {"q": "供应商选择？", "a": ""},
        ]},
        {"person": "刘会计", "role": "制单", "date": "2026-01-14", "qa": [
            {"q": "对账周期？", "a": "月度"},
        ]},
    ]}
    result = _call(interview_record, "audit.field.interview-record",
                   {"interview-input": _write(tmp_path, "interviews.json", payload)})
    assert result["contract_id"] == "document-content"
    assert len(result["artifact"]["chapters"]) == 2
    assert result["artifact"]["summary"]["total_questions"] == 3
    assert result["artifact"]["summary"]["answered"] == 2
    assert result["artifact"]["chapters"][0]["body"]["unanswered_count"] == 1


# ---------- field: inventory-count ----------

def test_inventory_count_flags_differences(tmp_path: Path) -> None:
    payload = {"period": "2026-01", "items": [
        {"sku": "S001", "name": "原料A", "book_qty": 10, "count_qty": 10},
        {"sku": "S002", "name": "原料B", "book_qty": 10, "count_qty": 5},
    ]}
    result = _call(inventory_count, "audit.field.inventory-count",
                   {"inventory-input": _write(tmp_path, "inventory.json", payload)})
    assert result["contract_id"] == "dataset-validation"
    assert result["summary"]["valid"] is False
    assert result["summary"]["checked_rows"] == 2
    assert len(result["violations"]) == 1
    assert "S002" in result["violations"][0]["message"]
    assert result["violations"][0]["check_id"] == "columns"


# ---------- field: meeting-minutes ----------

def test_meeting_minutes_extracts_key_points(tmp_path: Path) -> None:
    payload = {"meeting_id": "M1", "segments": [
        {"speaker": "组长", "text": "今天主要讨论资金专项问题。", "ts": "2026-01-15T14:00:00Z"},
        {"speaker": "组长", "text": "决议：成立整改小组，一周内完成。", "ts": "2026-01-15T14:05:00Z"},
    ]}
    result = _call(meeting_minutes, "audit.field.meeting-minutes",
                   {"meeting-audio": _write(tmp_path, "meeting.json", payload)})
    assert result["contract_id"] == "document-content"
    assert result["artifact"]["summary"]["segments"] == 2
    assert result["artifact"]["summary"]["key_points"] == 1
    assert "整改小组" in result["artifact"]["key_points"][0]["text"]


# ---------- field: progress-report ----------

def test_progress_report_computes_rate(tmp_path: Path) -> None:
    payload = {"period": "2026-01", "date": "2026-01-15T00:00:00+08:00",
               "budget_hours": 10, "completed_hours": 7}
    result = _call(progress_report, "audit.field.progress-report",
                   {"daily-progress": _write(tmp_path, "budget.json", payload)})
    assert result["contract_id"] == "metric-series"
    assert result["metric"] == "completion_rate"
    assert result["points"] == [{"at": "2026-01-15T00:00:00+08:00", "value": 0.7}]


# ---------- field: asset-check ----------

def test_asset_check_flags_location_mismatch(tmp_path: Path) -> None:
    payload = {"assets": [
        {"asset_code": "AST-001", "name": "服务器", "book_loc": "机房A", "found_loc": "机房A"},
        {"asset_code": "AST-002", "name": "笔记本", "book_loc": "机房B", "found_loc": "仓库C"},
    ]}
    result = _call(asset_check, "audit.field.asset-check",
                   {"asset-input": _write(tmp_path, "assets.json", payload)})
    assert result["summary"]["valid"] is False
    assert result["summary"]["checked_rows"] == 2
    assert len(result["violations"]) == 1
    assert "AST-002" in result["violations"][0]["message"]


# ---------- field: voucher-drilldown ----------

def test_voucher_drilldown_accepts_finance_clean_rows(tmp_path: Path) -> None:
    rows = [
        {"entry_id": "E9301", "date": "2026-01-05", "account_code": "1001",
         "description": "大额支出", "debit_amount": 1200000.0, "credit_amount": 0.0},
        {"entry_id": "E9201", "date": "2026-02-03", "account_code": "1002",
         "description": "跨期入账", "debit_amount": 50000.0, "credit_amount": 0.0},
    ]
    port = {"contract_id": "ledger-artifact-ref", "contract_version": "1.0.0",
            "schema_mapping_version": "1.0.0", "period": "2026-01", "rows": rows}
    envelope = {"protocol": "audit-network-plugin-child-v1",
                "plugin_id": "audit.field.voucher-drilldown",
                "capability": "audit.field.voucher-drilldown",
                "trace_id": str(uuid4()), "clean-finance-set": port}
    result = voucher_drilldown.handle(envelope)
    meta = result["artifact"]["metadata"]
    assert meta["drilldown_count"] == 2
    assert meta["sample_e9301"]["voucher"] == "V-E9301"


def test_voucher_drilldown_builds_three_level_chain(tmp_path: Path) -> None:
    csv_text = "entry_id,date,account_code,description,debit_amount,credit_amount\n" \
               "E9301,2026-01-05,1001,大额支出,1200000,0\n" \
               "E9201,2026-02-03,1002,跨期入账,50000,0\n"
    artifact = _write(tmp_path, "ledger.csv", csv_text.encode("utf-8"))
    port = {
        "contract_id": "ledger-artifact-ref", "contract_version": "1.0.0",
        "schema_mapping_version": "1.0.0", "period": "2026-01",
        "artifact": {"uri": artifact.uri, "sha256": artifact.sha256, "size_bytes": artifact.size_bytes},
    }
    envelope = {"protocol": "audit-network-plugin-child-v1",
                "plugin_id": "audit.field.voucher-drilldown",
                "capability": "audit.field.voucher-drilldown",
                "trace_id": str(uuid4()), "clean-finance-set": port}
    result = voucher_drilldown.handle(envelope)
    assert result["contract_id"] == "ledger-artifact-ref"
    meta = result["artifact"]["metadata"]
    assert meta["drilldown_levels"] == ["entry", "voucher", "source_doc"]
    assert meta["drilldown_count"] == 2
    assert meta["sample_e9301"] == {"entry": "E9301", "voucher": "V-E9301",
                                    "source_doc": "SD-E9301", "account": "1001", "date": "2026-01-05"}


# ---------- field: workpaper-build ----------

def test_workpaper_build_emits_draft_sections(tmp_path: Path) -> None:
    template = {"engagement_id": "ENG-2026-001", "engagement_name": "资金专项审计",
                "template_id": "TPL-1", "procedures": [
                    {"name": "凭证抽查", "detail": "对全部大额凭证逐笔核查"},
                    {"name": "函证核对", "detail": "向供应商发函核对余额"},
                ]}
    size = {"contract_id": "metric-series", "contract_version": "1.0.0",
            "series_id": "sample-1", "metric": "sample_size", "unit": "count",
            "window_minutes": 1440, "points": [{"at": "2026-01-15T00:00:00+08:00", "value": 5}]}
    result = _call(workpaper_build, "audit.field.workpaper-build",
                   {"program-template": _write(tmp_path, "template.json", template),
                    "sample-size": _write(tmp_path, "size.json", size)})
    assert result["contract_id"] == "workpaper-export"
    assert result["status"] == "draft"
    assert len(result["sections"]) == 2
    assert result["sections"][0]["severity"] == "low"
    assert result["sections"][0]["reviewer_label"] == "待复核"
    assert result["summary"]["low"] == 2
    assert result["constraints"] == {"no_overwrite": True, "requires_human_approval": True,
                                     "immutable_source": True}
    assert result["provenance"]["plugin"] == "audit.workpaper-export@0.1.0"
