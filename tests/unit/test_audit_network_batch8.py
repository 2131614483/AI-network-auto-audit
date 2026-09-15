# -*- coding: utf-8 -*-
"""Batch H (report stage) plugin runtime tests.

Data-source policy (user hard constraint): report-stage tests MUST reference
the real audit-material library at E:\\数据 where available; otherwise the
test uses simulated data and marks itself as such in the test output.

Real samples referenced:
  - E:\\数据\\03-AI审计技能包\\nigo-skills\\audit-report-checker\\references\\rules.md
      (real report cross-check rule library, L2)
  - E:\\数据\\04-审计数据集与基准\\PCCA-Benchmark\\benchmark_cases\\bigcase\\BCSA\\BCSA_big_01\\expert_summary.json
      (real PCCA industrial audit expert summary)
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

from plugins.builtin.audit_report_advice_match.runtime import handle as advice_match_handle
from plugins.builtin.audit_report_notice_mask_publish.runtime import handle as publish_handle
from plugins.builtin.audit_report_report_data_check.runtime import handle as data_check_handle
from plugins.builtin.audit_report_report_frame_build.runtime import handle as frame_build_handle
from plugins.builtin.audit_report_report_multi_review.runtime import handle as multi_review_handle
from plugins.builtin.audit_report_result_distill.runtime import handle as distill_handle

REAL_RULES_MD = Path(r"E:\数据\03-AI审计技能包\nigo-skills\audit-report-checker\references\rules.md")
REAL_EXPERT_SUMMARY = Path(
    r"E:\数据\04-审计数据集与基准\PCCA-Benchmark\benchmark_cases\bigcase\BCSA\BCSA_big_01\expert_summary.json"
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
        "trace_id": f"trace-batch8-{uuid4().hex[:8]}",
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
def _sources() -> dict[str, tuple[Any, str]]:
    def read(path: Path, what: str) -> tuple[Any, str]:
        if path.exists():
            if path.suffix.lower() == ".json":
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh), f"real:{path}"
            return path.read_text(encoding="utf-8"), f"real:{path}"
        fallback: Any = (
            {"expert_profile": "senior_engineer", "total_documents": 124}
            if what == "expert"
            else "模拟规则文本（E:\\数据 不可用）"
        )
        print(f"[batch8-data] {what}: SIMULATED (E:\\数据 sample not found at {path})")
        return fallback, "simulated"

    rules, rules_src = read(REAL_RULES_MD, "rules")
    expert, expert_src = read(REAL_EXPERT_SUMMARY, "expert")
    print(f"[batch8-data] rules -> {rules_src}")
    print(f"[batch8-data] expert -> {expert_src}")
    return {"rules": (rules, rules_src), "expert": (expert, expert_src)}


def test_frame_build_builds_skeleton(_sources) -> None:
    expert, expert_src = _sources["expert"]
    checks = [
        {"check_id": "columns", "status": "pass", "message": "project:PR-0001 score:0.92 admitted:true"},
        {"check_id": "columns", "status": "pass", "message": "project:PR-0002 score:0.77 admitted:true"},
    ]
    payload = {
        "snapshot_sha256": "a" * 64,
        "summary": {"valid": True},
        "checks": checks,
        "violations": [],
        "expert": expert,
    }
    out = frame_build_handle(_env("audit.report.report-frame-build", project_snapshot=payload))
    assert out["report_kind"] == "frame"
    assert len(out["sections"]) == 8
    assert len(out["projects"]) == 2
    assert out["projects"][0]["project_id"] == "PR-0001"
    assert out["projects"][0]["score"] == pytest.approx(0.92)
    assert out["template_version"] == "2026.1.0"
    if expert_src.startswith("real:"):
        assert out["expert_profile"] == "senior_engineer"
    print(f"[batch8-ok] frame-build sections=8 projects=2 expert_src={expert_src}")


def test_advice_match_categories(_sources) -> None:
    rules, rules_src = _sources["rules"]
    categories = ["财务核算", "资金管理", "采购管理", "内控缺陷", "舞弊风险", "其他"]
    findings = [
        {
            "issue_id": f"ISSUE-{i + 1:04d}",
            "category": cat,
            "severity": "high" if i % 2 == 0 else "medium",
            "amount": (i + 1) * 12345.67,
            "rule_key": "cross-check",
            "row_ref": f"row-{i}",
            "recommended_action": "限期整改",
        }
        for i, cat in enumerate(categories)
    ]
    out = advice_match_handle(_env("audit.report.advice-match", final_issue_set={"findings": findings, "source_ref": rules}))
    assert out["summary"]["advice_count"] == 6
    assert out["advices"][0]["category"] == "财务核算"
    assert "完善" in out["advices"][0]["advice"]
    print(f"[batch8-ok] advice-match advices=6 rules_src={rules_src}")


def test_report_data_check_valid_and_violations() -> None:
    good = {
        "sections": ["封面", "审计概况", "主要审计发现", "审计结论"],
        "summary": {"issue_count": 3},
        "issues": [{"issue_id": "ISSUE-0001"}, {"issue_id": "ISSUE-0002"}, {"issue_id": "ISSUE-0003"}],
    }
    ok = data_check_handle(_env("audit.report.report-data-check", report_draft_input=good))
    assert ok["summary"]["valid"] is True
    assert all(c["status"] == "pass" for c in ok["checks"] if c["check_id"] in ("columns", "freshness"))

    bad = {"sections": [], "summary": {"issue_count": 0}}
    rej = data_check_handle(_env("audit.report.report-data-check", report_draft_input=bad))
    assert rej["summary"]["valid"] is False
    assert any(v["check_id"] == "columns" for v in rej["violations"])
    print("[batch8-ok] report-data-check valid=1 violations=1")


def test_multi_review_approves_when_clean() -> None:
    clean = {
        "snapshot_sha256": "0" * 64,
        "summary": {"valid": True, "checked_columns": 3, "checked_rows": 4, "missing_values": 0},
        "checks": [{"check_id": "columns", "status": "pass", "message": "ok"}],
        "violations": [],
    }
    out = multi_review_handle(_env("audit.report.report-multi-review", report_check_report=clean))
    assert out["status"] == "approved"
    assert out["summary"]["approved"] is True
    assert out["summary"]["review_levels"] == ["项目负责人", "部门负责人", "总审计师"]

    dirty = dict(clean)
    dirty["violations"] = [{"check_id": "columns", "message": "章节缺失"}]
    dirty["summary"]["valid"] = False
    rej = multi_review_handle(_env("audit.report.report-multi-review", report_check_report=dirty))
    assert rej["status"] == "rejected"
    assert rej["summary"]["approved"] is False
    print("[batch8-ok] multi-review approve=1 reject=1")


def test_result_distill_requires_approval() -> None:
    approved = {"summary": {"approved": True, "violation_count": 0}}
    out = distill_handle(_env("audit.report.result-distill", report_approved=approved))
    assert out["artifact"]["distilled"] is True
    assert len(out["artifact"]["insights"]) == 3

    rejected = {"summary": {"approved": False, "violation_count": 2}}
    with pytest.raises(Exception):
        distill_handle(_env("audit.report.result-distill", report_approved=rejected))
    print("[batch8-ok] distill approved=1 rejected-gated=1")


def test_notice_mask_publish_masks_fields() -> None:
    approved = {"summary": {"approved": True, "violation_count": 0}}
    out = publish_handle(_env("audit.report.notice-mask-publish", report_approved=approved))
    assert out["artifact"]["masked"] is True
    assert "amount" in out["artifact"]["mask_fields"]
    assert "审计公告" in out["artifact"]["title"]
    print("[batch8-ok] notice-mask-publish masked=1")


def test_batch8_uses_real_data_source_when_available() -> None:
    """Hard constraint check: E:\\数据 real samples must exist for report tests."""
    if REAL_RULES_MD.exists() and REAL_EXPERT_SUMMARY.exists():
        assert REAL_RULES_MD.read_text(encoding="utf-8").strip()
        expert = json.loads(REAL_EXPERT_SUMMARY.read_text(encoding="utf-8"))
        assert expert["total_documents"] >= 100
        print(f"[batch8-data] E:\\数据 available: rules={REAL_RULES_MD} expert={REAL_EXPERT_SUMMARY}")
    else:
        print("[batch8-data] E:\\数据 NOT available -> report tests used SIMULATED data (noted)")
        pytest.skip("E:\\数据 real audit-material library is not mounted; simulated data used (noted)")
