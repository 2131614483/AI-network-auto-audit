"""Unit tests for network batch 6: risk stage (风险识别与评估) 9 plugins —
the risk-scan / matrix / level / area / heatmap / advice chain."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput
from plugins.builtin.audit_risk_fraud_risk_match import runtime as fraud_match
from plugins.builtin.audit_risk_high_risk_area_locate import runtime as area_locate
from plugins.builtin.audit_risk_industry_risk_benchmark import runtime as industry_benchmark
from plugins.builtin.audit_risk_internal_control_risk_map import runtime as ic_map
from plugins.builtin.audit_risk_macro_policy_risk_scan import runtime as policy_scan
from plugins.builtin.audit_risk_process_gap_detect import runtime as process_gap
from plugins.builtin.audit_risk_risk_advice_generate import runtime as advice_generate
from plugins.builtin.audit_risk_risk_heatmap_draw import runtime as heatmap_draw
from plugins.builtin.audit_risk_risk_level_assign import runtime as level_assign

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / ".data" / "audit-sim"


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path) -> None:
    os.environ["AUDIT_PLUGIN_READ_ROOTS"] = json.dumps([str(SIM), str(SIM.parent), str(tmp_path)])


def _write(tmp_path: Path, name: str, data) -> ArtifactInput:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="application/json", sha256=hashlib.sha256(raw).hexdigest(),
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


def _doc(**artifact_fields) -> dict:
    return {"contract_id": "document-content", "contract_version": "1.0.0",
            "artifact": {"title": "x", **artifact_fields}}


def _risk_level_points() -> list[dict]:
    return [
        {"at": "2026-01-01T00:00:00+08:00", "value": 0.9, "risk_id": "r1",
         "channel": "财务异常", "row_ref": "ledger:E9301", "level": "high"},
        {"at": "2026-01-01T00:00:00+08:00", "value": 0.5, "risk_id": "r2",
         "channel": "内控流程", "row_ref": "ic-step:2", "level": "medium"},
        {"at": "2026-01-01T00:00:00+08:00", "value": 0.2, "risk_id": "r3",
         "channel": "行业对标", "row_ref": "industry:1", "level": "low"},
    ]


def _risk_level_series() -> dict:
    return {"contract_id": "metric-series", "contract_version": "1.0.0",
            "series_id": "risk-level-2026", "metric": "risk_score", "unit": "score",
            "window_minutes": 1440, "points": _risk_level_points()}


# ---------- risk: macro-policy-risk-scan ----------

def test_policy_scan_hits_lexicon(tmp_path: Path) -> None:
    payload = _doc(period="2026", policies=[
        {"policy_id": "P1", "title": "大额资金支付监管办法", "content": "对大额资金流向实施穿透核查"},
        {"policy_id": "P2", "title": "员工考勤制度", "content": "规范考勤管理"},
    ])
    result = _call(policy_scan, "audit.risk.macro-policy-risk-scan",
                   {"policy-input": _write(tmp_path, "policy.json", payload)})
    assert result["summary"]["candidates"] == 1
    assert result["candidates"][0]["rule_key"] == "policy_breach"
    assert result["candidates"][0]["row_ref"] == "policy:P1"


# ---------- risk: industry-risk-benchmark ----------

def test_industry_benchmark_passthrough(tmp_path: Path) -> None:
    payload = _doc(period="2026", industry_risks=[
        {"risk_id": "I1", "name": "供应商舞弊高发", "severity": "high", "score": 0.8},
        {"risk_id": "I2", "name": "应收账期恶化", "severity": "medium", "score": 0.5},
    ])
    result = _call(industry_benchmark, "audit.risk.industry-risk-benchmark",
                   {"industry-benchmark": _write(tmp_path, "ind.json", payload)})
    assert result["summary"]["candidates"] == 2
    assert result["candidates"][0]["rule_id"] == "IND-0001"


# ---------- risk: internal-control-risk-map ----------

def test_ic_map_flags_missing_control(tmp_path: Path) -> None:
    payload = _doc(period="2026", control_steps=[
        {"step_id": "S1", "name": "付款申请", "control": True, "approval": True},
        {"step_id": "S2", "name": "资金支付", "control": False, "approval": True},
        {"step_id": "S3", "name": "对账复核", "control": True, "approval": False},
    ])
    result = _call(ic_map, "audit.risk.internal-control-risk-map",
                   {"ic-flow": _write(tmp_path, "ic.json", payload)})
    assert result["summary"]["candidates"] == 2
    assert result["candidates"][0]["row_ref"] == "ic-step:S2"


# ---------- risk: process-gap-detect ----------

def test_process_gap_detects_broken_steps(tmp_path: Path) -> None:
    payload = _doc(period="2026", process_steps=[
        {"step_id": "P1", "name": "合同签订", "owner": "采购部", "approval": True},
        {"step_id": "P2", "name": "收货入库", "owner": "", "approval": True},
    ])
    result = _call(process_gap, "audit.risk.process-gap-detect",
                   {"process-input": _write(tmp_path, "proc.json", payload)})
    assert result["summary"]["candidates"] == 1
    assert "缺少责任人" in result["candidates"][0]["note"]


# ---------- risk: fraud-risk-match ----------

def test_fraud_match_two_signals_minimum(tmp_path: Path) -> None:
    payload = _doc(period="2026", fraud_scenarios=[
        {"scenario_id": "F1", "name": "虚构供应商", "pressure": True, "opportunity": True, "rationalization": True},
        {"scenario_id": "F2", "name": "审批流于形式", "opportunity": True, "rationalization": False, "pressure": False},
    ])
    result = _call(fraud_match, "audit.risk.fraud-risk-match",
                   {"fraud-input": _write(tmp_path, "fraud.json", payload)})
    assert result["summary"]["candidates"] == 1
    assert result["candidates"][0]["severity"] == "high"
    assert result["candidates"][0]["row_ref"] == "fraud:F1"


# ---------- risk: risk-level-assign ----------

def test_risk_level_assign_thresholds(tmp_path: Path) -> None:
    matrix = {"contract_id": "risk-matrix", "contract_version": "1.0.0",
              "period": "2026",
              "points": [
                  {"risk_id": "a", "score": 0.9, "channel": "财务异常", "row_ref": "x"},
                  {"risk_id": "b", "score": 0.5, "channel": "内控流程", "row_ref": "y"},
                  {"risk_id": "c", "score": 0.2, "channel": "行业对标", "row_ref": "z"},
              ]}
    result = _call(level_assign, "audit.risk.risk-level-assign",
                   {"risk-matrix": _write(tmp_path, "matrix.json", matrix)})
    assert [p["level"] for p in result["points"]] == ["high", "medium", "low"]


# ---------- risk: high-risk-area-locate ----------

def test_high_risk_area_filters_low(tmp_path: Path) -> None:
    result = _call(area_locate, "audit.risk.high-risk-area-locate",
                   {"risk-level": _write(tmp_path, "level.json", _risk_level_series())})
    assert len(result["points"]) == 2
    assert {p["label"] for p in result["points"]} == {"资金收付"}


# ---------- risk: risk-heatmap-draw ----------

def test_heatmap_draw_matrix(tmp_path: Path) -> None:
    result = _call(heatmap_draw, "audit.risk.risk-heatmap-draw",
                   {"risk-level": _write(tmp_path, "level2.json", _risk_level_series())})
    assert result["artifact"]["summary"]["total_risks"] == 3
    assert result["artifact"]["matrix"]["财务异常"]["high"] == 1
    assert result["artifact"]["matrix"]["行业对标"]["low"] == 1


# ---------- risk: risk-advice-generate ----------

def test_advice_generate_matches_area(tmp_path: Path) -> None:
    area_series = {"contract_id": "metric-series", "contract_version": "1.0.0",
                   "series_id": "high-risk-area-2026", "metric": "risk_score", "unit": "score",
                   "window_minutes": 1440, "points": [
                       {"at": "2026-01-01T00:00:00+08:00", "value": 0.9,
                        "label": "资金收付", "risk_id": "r1", "level": "high"},
                       {"at": "2026-01-01T00:00:00+08:00", "value": 0.8,
                        "label": "资金收付", "risk_id": "r2", "level": "medium"},
                   ]}
    result = _call(advice_generate, "audit.risk.risk-advice-generate",
                   {"high-risk-area": _write(tmp_path, "area.json", area_series)})
    assert len(result["artifact"]["advices"]) == 1
    assert "资金" in result["artifact"]["advices"][0]["advice"]
