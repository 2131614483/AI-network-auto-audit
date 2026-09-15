"""Unit tests for network batch 5: mandate (立项) 10 + plan (计划) 9 plugins —
the workflow start segment (audit.mandate.* / audit.plan.*)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput
from plugins.builtin.audit_mandate_annual_propose import runtime as annual_propose
from plugins.builtin.audit_mandate_demand_collect import runtime as demand_collect
from plugins.builtin.audit_mandate_material_precheck import runtime as material_precheck
from plugins.builtin.audit_mandate_material_submit import runtime as material_submit
from plugins.builtin.audit_mandate_notice_generate import runtime as notice_generate
from plugins.builtin.audit_mandate_priority_rank import runtime as priority_rank
from plugins.builtin.audit_mandate_project_library import runtime as project_library
from plugins.builtin.audit_mandate_proposal_score import runtime as proposal_score
from plugins.builtin.audit_mandate_strategy_align import runtime as strategy_align
from plugins.builtin.audit_mandate_team_forming import runtime as team_forming
from plugins.builtin.audit_plan_annual_plan_build import runtime as annual_plan_build
from plugins.builtin.audit_plan_effort_budget import runtime as effort_budget
from plugins.builtin.audit_plan_plan_version_control import runtime as plan_version_control
from plugins.builtin.audit_plan_program_template_match import runtime as program_template_match
from plugins.builtin.audit_plan_project_scheme_build import runtime as project_scheme_build
from plugins.builtin.audit_plan_resource_conflict_detect import runtime as resource_conflict
from plugins.builtin.audit_plan_sample_size_compute import runtime as sample_size_compute
from plugins.builtin.audit_plan_sampling_select import runtime as sampling_select
from plugins.builtin.audit_plan_staff_schedule import runtime as staff_schedule

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


def _demand(demand_id: str, dept: str, title: str, priority: str, detail: str) -> dict:
    return {"demand_id": demand_id, "dept": dept, "title": title, "priority": priority, "detail": detail}


def _demand_set(demands: list[dict]) -> dict:
    return {"contract_id": "document-content", "contract_version": "1.0.0",
            "artifact": {"title": "需求", "demands": demands}}


# ---------- mandate: demand-collect ----------

def test_demand_collect_normalises_demands(tmp_path: Path) -> None:
    payload = {"period": "2026", "demands": [
        _demand("DM-0001", "财务部", "资金流向核查", "high", "核查大额资金流向"),
        _demand("DM-0002", "采购部", "采购合规检查", "medium", "抽查采购合同"),
    ]}
    result = _call(demand_collect, "audit.mandate.demand-collect",
                   {"demand-input": _write(tmp_path, "demands.json", payload)})
    assert result["artifact"]["summary"]["demand_count"] == 2
    assert result["artifact"]["demands"][0]["dept"] == "财务部"


# ---------- mandate: strategy-align ----------

def test_strategy_align_marks_alignment(tmp_path: Path) -> None:
    demands = [_demand("DM-0001", "财务部", "资金流向核查", "high", "核查大额资金流向"),
               _demand("DM-0002", "运营部", "日常费用报销", "low", "一般性复核")]
    result = _call(strategy_align, "audit.mandate.strategy-align",
                   {"demand-set": _write(tmp_path, "demand-set.json", _demand_set(demands))})
    by_id = {d["demand_id"]: d for d in result["artifact"]["demands"]}
    assert by_id["DM-0001"]["alignment"] == "high"
    assert by_id["DM-0001"]["aligned_markers"] == ["资金"]
    assert by_id["DM-0002"]["alignment"] == "待人工确认"


# ---------- mandate: annual-propose ----------

def test_annual_propose_only_aligned_become_proposals(tmp_path: Path) -> None:
    demands = [_demand("DM-0001", "财务部", "资金流向核查", "high", "核查大额资金流向"),
               _demand("DM-0002", "运营部", "日常费用报销", "low", "一般性复核")]
    aligned = _demand_set(demands)
    aligned["artifact"]["demands"][0]["alignment"] = "high"
    aligned["artifact"]["demands"][1]["alignment"] = "待人工确认"
    result = _call(annual_propose, "audit.mandate.annual-propose",
                   {"aligned-demand": _write(tmp_path, "aligned.json", aligned)})
    assert result["artifact"]["summary"]["proposal_count"] == 1
    assert result["artifact"]["proposals"][0]["source_demand"] == "DM-0001"


# ---------- mandate: proposal-score ----------

def test_proposal_score_by_priority(tmp_path: Path) -> None:
    proposal_set = {"contract_id": "document-content", "contract_version": "1.0.0",
                    "artifact": {"proposals": [
                        {"proposal_id": "PR-0001", "priority": "high"},
                        {"proposal_id": "PR-0002", "priority": "medium"},
                    ]}}
    result = _call(proposal_score, "audit.mandate.proposal-score",
                   {"proposal-set": _write(tmp_path, "proposals.json", proposal_set)})
    assert [p["value"] for p in result["points"]] == [85.0, 70.0]


# ---------- mandate: project-library ----------

def test_project_library_registers_snapshot(tmp_path: Path) -> None:
    series = {"contract_id": "metric-series", "contract_version": "1.0.0",
              "series_id": "s", "metric": "proposal_score", "unit": "score",
              "window_minutes": 1440,
              "points": [{"at": "2026-01-01T00:00:00+08:00", "value": 85.0},
                         {"at": "2026-01-02T00:00:00+08:00", "value": 70.0}]}
    result = _call(project_library, "audit.mandate.project-library",
                   {"scored-proposal": _write(tmp_path, "scored.json", series)})
    assert result["summary"]["checked_rows"] == 2
    assert "project:PR-0001 score:85" in result["checks"][0]["message"]


# ---------- mandate: priority-rank ----------

def test_priority_rank_sorts_descending(tmp_path: Path) -> None:
    snapshot = {"contract_id": "dataset-validation", "contract_version": "1.0.0",
                "summary": {}, "checks": [
                    {"check_id": "columns", "status": "pass", "message": "project:PR-0001 score:70 admitted:true"},
                    {"check_id": "columns", "status": "pass", "message": "project:PR-0002 score:85 admitted:true"},
                ], "violations": []}
    result = _call(priority_rank, "audit.mandate.priority-rank",
                   {"project-snapshot": _write(tmp_path, "snapshot.json", snapshot)})
    assert [p["value"] for p in result["points"]] == [85.0, 70.0]


# ---------- mandate: notice-generate ----------

def test_notice_generate_picks_top_project(tmp_path: Path) -> None:
    snapshot = {"contract_id": "dataset-validation", "contract_version": "1.0.0",
                "summary": {}, "checks": [
                    {"check_id": "columns", "status": "pass", "message": "project:PR-0001 score:70 admitted:true"},
                    {"check_id": "columns", "status": "pass", "message": "project:PR-0002 score:85 admitted:true"},
                ], "violations": []}
    result = _call(notice_generate, "audit.mandate.notice-generate",
                   {"project-snapshot": _write(tmp_path, "snap.json", snapshot)})
    assert result["artifact"]["project"] == "PR-0002"
    assert result["artifact"]["status"] == "issued"


# ---------- mandate: material-submit ----------

def test_material_submit_registers_units(tmp_path: Path) -> None:
    payload = {"project": "PR-0002", "units": [
        {"unit_name": "财务部", "materials": ["通知书回执", "财务报表"], "submitted_at": "2026-01-10"},
        {"unit_name": "采购部", "materials": ["通知书回执"], "submitted_at": "2026-01-11"},
    ]}
    result = _call(material_submit, "audit.mandate.material-submit",
                   {"notice-accept": _write(tmp_path, "units.json", payload)})
    assert result["metadata"]["unit_count"] == 2
    assert result["metadata"]["units"][0]["material_count"] == 2


# ---------- mandate: material-precheck ----------

def test_material_precheck_flags_missing_required(tmp_path: Path) -> None:
    payload = {"metadata": {"units": [
        {"unit_name": "财务部", "materials": ["通知书回执", "营业执照", "财务报表", "资金流水"]},
    ]}}
    result = _call(material_precheck, "audit.mandate.material-precheck",
                   {"submitted-material": _write(tmp_path, "submitted.json", payload)})
    assert result["summary"]["valid"] is False
    assert len(result["violations"]) == 1
    assert "内控文档" in result["violations"][0]["message"]


# ---------- mandate: team-forming ----------

def test_team_forming_creates_role_skeleton(tmp_path: Path) -> None:
    snapshot = {"contract_id": "dataset-validation", "contract_version": "1.0.0",
                "summary": {}, "checks": [
                    {"check_id": "columns", "status": "pass", "message": "project:PR-0002 score:85 admitted:true"},
                ], "violations": []}
    result = _call(team_forming, "audit.mandate.team-forming",
                   {"project-snapshot": _write(tmp_path, "snap2.json", snapshot)})
    assert result["workflow_id"] == "team-PR-0002"
    assert [n["name"] for n in result["nodes"]] == ["组长", "主审", "助审", "复核"]
    assert all(n["props"]["pending_human"] for n in result["nodes"])


# ---------- plan: annual-plan-build ----------

def test_annual_plan_build_assigns_quarters(tmp_path: Path) -> None:
    series = {"contract_id": "metric-series", "contract_version": "1.0.0",
              "series_id": "priority-order-2026", "metric": "priority_order", "unit": "rank",
              "window_minutes": 1440,
              "points": [{"at": "2026-01-01T00:00:00+08:00", "value": 85.0},
                         {"at": "2026-01-02T00:00:00+08:00", "value": 70.0},
                         {"at": "2026-01-03T00:00:00+08:00", "value": 55.0}]}
    result = _call(annual_plan_build, "audit.plan.annual-plan-build",
                   {"priority-order": _write(tmp_path, "order.json", series)})
    assert result["artifact"]["summary"]["project_count"] == 3
    assert [p["quarter"] for p in result["artifact"]["projects"]] == ["Q1", "Q2", "Q3"]


# ---------- plan: project-scheme-build ----------

def test_project_scheme_build_scopes_areas(tmp_path: Path) -> None:
    risk = {"contract_id": "metric-series", "contract_version": "1.0.0",
            "series_id": "high-risk-1", "metric": "risk_score", "unit": "score",
            "window_minutes": 1440,
            "points": [{"at": "2026-01-01T00:00:00+08:00", "value": 0.9, "label": "资金收付"},
                       {"at": "2026-01-01T00:00:00+08:00", "value": 0.8, "label": "采购合同"}]}
    advices = {"contract_id": "document-content", "contract_version": "1.0.0",
               "artifact": {"advices": [{"advice": "重点核查大额资金流向"}]}}
    result = _call(project_scheme_build, "audit.plan.project-scheme-build",
                   {"high-risk-area": _write(tmp_path, "risk.json", risk),
                    "risk-advice-set": _write(tmp_path, "advice.json", advices)})
    assert len(result["artifact"]["scope_areas"]) == 2
    assert result["artifact"]["risk_advices"] == ["重点核查大额资金流向"]


# ---------- plan: program-template-match ----------

def test_program_template_match_known_and_unmatched(tmp_path: Path) -> None:
    request = {"contract_id": "document-content", "contract_version": "1.0.0",
               "artifact": {"areas": ["资金", "新兴领域"]}}
    result = _call(program_template_match, "audit.plan.program-template-match",
                   {"scheme-request": _write(tmp_path, "req.json", request)})
    assert result["artifact"]["summary"]["procedure_count"] == 2
    assert result["artifact"]["summary"]["unmatched_areas"] == ["新兴领域"]


# ---------- plan: sampling-select ----------

def test_sampling_select_covers_material_items(tmp_path: Path) -> None:
    payload = {"method": "materiality", "population": [
        {"item_id": "I1", "amount": 2000000},
        {"item_id": "I2", "amount": 50000},
        {"item_id": "I3", "amount": 30000},
        {"item_id": "I4", "amount": 10000},
    ]}
    result = _call(sampling_select, "audit.plan.sampling-select",
                   {"sampling-input": _write(tmp_path, "pop.json", payload)})
    message = result["checks"][0]["message"]
    assert "sample_size:2" in message
    assert message.split("items:")[1].startswith("I1,")


# ---------- plan: sample-size-compute ----------

def test_sample_size_compute_formula(tmp_path: Path) -> None:
    plan = {"contract_id": "dataset-validation", "contract_version": "1.0.0",
            "summary": {}, "checks": [
                {"check_id": "columns", "status": "pass",
                 "message": "method:materiality population:100 sample_size:2 items:I1,I2"},
            ], "violations": []}
    result = _call(sample_size_compute, "audit.plan.sample-size-compute",
                   {"sampling-plan": _write(tmp_path, "plan.json", plan)})
    assert result["points"][0]["value"] == 80.0


# ---------- plan: staff-schedule ----------

def test_staff_schedule_keeps_pending(tmp_path: Path) -> None:
    team = {"workflow_id": "team-PR-0002", "tenant_id": "t1", "key": "team-forming", "version": "1.0.0",
            "input_schema": {}, "output_schema": {}, "nodes": [
                {"id": "role-1", "name": "组长", "props": {"member": "张三", "pending_human": False}},
                {"id": "role-2", "name": "主审", "props": {"member": "", "pending_human": True}},
            ], "edges": []}
    result = _call(staff_schedule, "audit.plan.staff-schedule",
                   {"team-scheme": _write(tmp_path, "team.json", team)})
    assert result["checksum"]["days"] == 5
    assert result["checksum"]["assigned_members"] == 1
    assert result["nodes"][0]["props"]["slots"][0]["status"] == "assigned"
    assert result["nodes"][0]["props"]["slots"][1]["status"] == "pending_human"


# ---------- plan: effort-budget ----------

def test_effort_budget_computes_hours(tmp_path: Path) -> None:
    schedule = {"workflow_id": "schedule-1", "tenant_id": "t1", "key": "staff-schedule", "version": "1.0.0",
                "input_schema": {}, "output_schema": {}, "nodes": [
                    {"id": "day-1", "name": "D1", "props": {"slots": [
                        {"role": "组长", "member": "张三", "status": "assigned"}]}},
                    {"id": "day-2", "name": "D2", "props": {"slots": [
                        {"role": "组长", "member": "张三", "status": "assigned"}]}},
                ], "edges": []}
    result = _call(effort_budget, "audit.plan.effort-budget",
                   {"schedule-plan": _write(tmp_path, "schedule.json", schedule)})
    assert result["points"][0]["value"] == 16.0


# ---------- plan: resource-conflict-detect ----------

def test_resource_conflict_detects_cross_project(tmp_path: Path) -> None:
    schedule = {"workflow_id": "schedule-2", "tenant_id": "t1", "key": "staff-schedule", "version": "1.0.0",
                "input_schema": {}, "output_schema": {}, "nodes": [
                    {"id": "day-1", "name": "D1", "props": {"slots": [
                        {"role": "主审", "member": "李四", "status": "assigned", "project": "PR-0001"},
                        {"role": "主审", "member": "李四", "status": "assigned", "project": "PR-0002"}]}},
                ], "edges": []}
    result = _call(resource_conflict, "audit.plan.resource-conflict-detect",
                   {"schedule-plan": _write(tmp_path, "sc.json", schedule),
                    "project-snapshot": _write(tmp_path, "snap3.json",
                                               {"contract_id": "dataset-validation", "contract_version": "1.0.0",
                                                "summary": {}, "checks": [], "violations": []})})
    assert result["summary"]["valid"] is False
    assert len(result["violations"]) == 1
    assert "李四" in result["violations"][0]["message"]


# ---------- plan: plan-version-control ----------

def test_plan_version_control_chains_versions(tmp_path: Path) -> None:
    payload = {"plan_key": "annual-2026", "tenant_id": "t1", "changes": [
        {"description": "调整抽样比例", "author": "张审计"},
        {"description": "新增资金领域程序", "author": "李审计"},
    ]}
    result = _call(plan_version_control, "audit.plan.plan-version-control",
                   {"plan-change": _write(tmp_path, "changes.json", payload)})
    assert [n["name"] for n in result["nodes"]] == ["版本 v1", "版本 v2"]
    assert result["checksum"]["current"] == "v2"
