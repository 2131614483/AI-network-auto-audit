# -*- coding: utf-8 -*-
"""Batch J (governance layer) plugin runtime tests.

Data-source policy (user hard constraint): governance tests reference the real
audit-material library at E:\\数据 where available; otherwise simulated data and
marked as such.

Real sample referenced:
  - E:\\数据\\04-审计数据集与基准\\PCCA-Benchmark\\benchmark_cases\\bigcase\\BCSA\\BCSA_big_01\\ground_truth.json
      (real PCCA ground-truth issue set)
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from plugins.builtin.audit_govern_case_library_update.runtime import handle as case_lib_handle
from plugins.builtin.audit_govern_effect_evaluate.runtime import handle as effect_eval_handle
from plugins.builtin.audit_govern_issue_trend_analysis.runtime import handle as trend_handle
from plugins.builtin.audit_govern_method_distill.runtime import handle as method_distill_handle
from plugins.builtin.audit_govern_project_quality_score.runtime import (
    handle as quality_score_handle,
)
from plugins.builtin.audit_govern_rule_iteration.runtime import handle as rule_iter_handle

REAL_GROUND_TRUTH = Path(
    r"E:\数据\04-审计数据集与基准\PCCA-Benchmark\benchmark_cases\bigcase\BCSA\BCSA_big_01\ground_truth.json"
)

_SHARED_TMP: Path | None = None


def _write(payload: dict[str, Any]):
    global _SHARED_TMP
    if _SHARED_TMP is None:
        _SHARED_TMP = Path(tempfile.mkdtemp())
        os.environ["AUDIT_PLUGIN_READ_ROOTS"] = json.dumps([str(_SHARED_TMP)])
    path = _SHARED_TMP / f"in-{uuid4().hex}.json"
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return {
        "uri": path.resolve().as_uri(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def _env(plugin_id: str, **ports) -> dict[str, Any]:
    env: dict[str, Any] = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": plugin_id,
        "capability": plugin_id,
        "trace_id": f"trace-batchJ-{uuid4().hex[:8]}",
    }
    for name, payload in ports.items():
        art = _write(payload)
        env[name.replace("_", "-")] = {
            "contract_id": "artifact-ref",
            "contract_version": "1.0.0",
            "artifact": {"uri": art["uri"], "sha256": art["sha256"], "size_bytes": art["size_bytes"]},
        }
    return env


@pytest.fixture(scope="module")
def _real_ground_truth() -> tuple[dict[str, Any], str]:
    if REAL_GROUND_TRUTH.exists():
        data = json.loads(REAL_GROUND_TRUTH.read_text(encoding="utf-8"))
        print(f"[batchJ-data] ground_truth -> real:{REAL_GROUND_TRUTH}")
        return data, "real"
    print(f"[batchJ-data] ground_truth -> SIMULATED (not found at {REAL_GROUND_TRUTH})")
    return {"issues": [{"category": "资金管理", "severity": "high"}, {"category": "采购管理", "severity": "medium"}]}, "simulated"


def test_project_quality_score_computes_ratio() -> None:
    flow_input = {
        "checks": [
            {"check_id": "columns", "status": "pass", "message": "ok"},
            {"check_id": "freshness", "status": "pass", "message": "ok"},
            {"check_id": "missing_rate", "status": "warn", "message": "warn"},
        ],
    }
    out = quality_score_handle(_env("audit.govern.project-quality-score", project_flow_input=flow_input))
    assert out["summary"]["score"] == pytest.approx(2 / 3)
    assert out["series_id"] == "audit-project-quality"
    print("[batchJ-ok] quality-score 2/3")


def test_effect_evaluate_from_closed_ledger() -> None:
    ledger = {
        "workflow_id": "remedy-close-2026",
        "nodes": [{"task_id": "RT-1", "status": "closed"}, {"task_id": "RT-2", "status": "closed"}, {"task_id": "RT-3", "status": "open"}],
        "summary": {"closed": True},
    }
    out = effect_eval_handle(_env("audit.govern.effect-evaluate", remedy_ledger=ledger))
    assert out["summary"]["closed"] == 2
    assert out["summary"]["total"] == 3
    assert out["summary"]["effect_ratio"] == pytest.approx(2 / 3)
    print("[batchJ-ok] effect-evaluate 2/3 closed")


def test_case_library_ingests_insights() -> None:
    distilled = {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "title": "审计成果提炼",
            "summary": "共性问题",
            "insights": ["资金支付审批流于形式", "采购验收缺留痕", "建议迭代规则"],
        },
    }
    out = case_lib_handle(_env("audit.govern.case-library-update", distilled_result=distilled))
    assert out["artifact"]["updated"] is True
    assert len(out["artifact"]["entries"]) == 3
    print("[batchJ-ok] case-library 3 entries")


def test_trend_and_rule_iteration(_real_ground_truth) -> None:
    gt, src = _real_ground_truth
    issues = gt.get("issues") or gt.get("findings") or []
    if not issues:
        issues = [
            {"category": "资金管理", "severity": "high"}, {"category": "资金管理", "severity": "medium"},
            {"category": "采购管理", "severity": "low"}, {"category": "内控缺陷", "severity": "high"},
        ]
    findings = [{"category": str(i.get("category", "其他")), "severity": str(i.get("severity", "low"))} for i in issues[:10]]
    trend = trend_handle(_env("audit.govern.issue-trend-analysis", history_issue_set={"findings": findings}))
    assert trend["summary"]["total"] == len(findings)
    assert trend["summary"]["high"] >= 1
    assert trend["summary"]["top_categories"]

    rulepack = rule_iter_handle(_env("audit.govern.rule-iteration", trend_report=trend))
    assert rulepack["summary"]["valid"] is True
    assert len(rulepack["rules"]) == len(trend["summary"]["top_categories"])
    print(f"[batchJ-ok] trend total={len(findings)} rules={len(rulepack['rules'])} src={src}")


def test_method_distill_from_review_notes() -> None:
    review = {
        "contract_id": "document-content", "contract_version": "1.0.0",
        "artifact": {
            "review_notes": ["凭证穿透核查法", "供应商函证全量法", "内控穿行测试法"],
        },
    }
    out = method_distill_handle(_env("audit.govern.method-distill", project_review_input=review))
    assert out["artifact"]["distilled"] is True
    assert len(out["artifact"]["methods"]) == 3
    print("[batchJ-ok] method-distill 3 methods")


def test_batchJ_uses_real_data_source_when_available() -> None:
    if REAL_GROUND_TRUTH.exists():
        gt = json.loads(REAL_GROUND_TRUTH.read_text(encoding="utf-8"))
        assert isinstance(gt, dict)
        print(f"[batchJ-data] E:\\数据 available: ground_truth keys={list(gt.keys())[:5]}")
    else:
        print("[batchJ-data] E:\\数据 NOT available -> govern tests used SIMULATED data (noted)")
        pytest.skip("E:\\数据 not mounted; simulated data used (noted)")
