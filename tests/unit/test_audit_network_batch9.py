# -*- coding: utf-8 -*-
"""Batch I (remedy stage) plugin runtime tests.

Data-source policy (user hard constraint): remedy-stage tests reference the
real audit-material library at E:\\数据 where available; otherwise the test
uses simulated data and marks itself as such.

Real sample referenced:
  - E:\\数据\\04-审计数据集与基准\\PCCA-Benchmark\\benchmark_cases\\bigcase\\BCSA\\BCSA_big_01\\fault_tickets.json
      (real PCCA industrial remedy tickets: root cause, resolution, status)
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

from plugins.builtin.audit_remedy_remedy_close.runtime import handle as close_handle
from plugins.builtin.audit_remedy_remedy_dispatch.runtime import handle as dispatch_handle
from plugins.builtin.audit_remedy_remedy_effect_verify.runtime import handle as effect_verify_handle
from plugins.builtin.audit_remedy_remedy_overdue_alert.runtime import handle as overdue_alert_handle
from plugins.builtin.audit_remedy_remedy_plan_review.runtime import handle as plan_review_handle
from plugins.builtin.audit_remedy_remedy_publish.runtime import handle as publish_handle

REAL_FAULT_TICKETS = Path(
    r"E:\数据\04-审计数据集与基准\PCCA-Benchmark\benchmark_cases\bigcase\BCSA\BCSA_big_01\fault_tickets.json"
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
        "trace_id": f"trace-batch9-{uuid4().hex[:8]}",
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
def _real_tickets() -> tuple[list[dict[str, Any]], str]:
    if REAL_FAULT_TICKETS.exists():
        tickets = json.loads(REAL_FAULT_TICKETS.read_text(encoding="utf-8"))
        assert isinstance(tickets, list) and tickets
        print(f"[batch9-data] fault_tickets -> real:{REAL_FAULT_TICKETS} ({len(tickets)} tickets)")
        return tickets, "real"
    print(f"[batch9-data] fault_tickets -> SIMULATED (E:\\数据 sample not found at {REAL_FAULT_TICKETS})")
    # Four entries, because that is the smallest set the assertions below need
    # (`tickets[:4]` -> task_count 4; `tickets[:3]` -> task_count 3 with an
    # owner to clear).  The previous fallback had only two, so both tests failed
    # with `assert 2 == 4` / `assert 2 == 3` on every machine that does not have
    # the external `E:\数据` drive mounted -- CI included.  The real file is still
    # preferred whenever it is present.
    simulated = [
        {"ticket_id": "FT_1", "fault_description": "溶剂残留", "severity": "中",
         "resolved_by": "domain_expert", "solution_applied": "环境适应性维护", "status": "已解决"},
        {"ticket_id": "FT_2", "fault_description": "喷头堵塞", "severity": "中",
         "resolved_by": "maintenance_specialist", "solution_applied": "智能湿度补偿", "status": "处理中"},
        {"ticket_id": "FT_3", "fault_description": "温控漂移", "severity": "高",
         "resolved_by": "instrument_engineer", "solution_applied": "重新标定温控回路", "status": "已解决"},
        {"ticket_id": "FT_4", "fault_description": "供料脉动", "severity": "低",
         "resolved_by": "process_engineer", "solution_applied": "加装阻尼稳压", "status": "处理中"},
    ]
    return simulated, "simulated"


def test_dispatch_creates_tasks_from_issues(_real_tickets) -> None:
    tickets, src = _real_tickets
    findings = [
        {
            "issue_id": f"ISSUE-{i + 1:04d}",
            "category": "内控缺陷",
            "severity": "high" if i % 2 == 0 else "medium",
            "amount": 100000 + i,
            "rule_key": "remedy",
            "row_ref": str(t.get("ticket_id", "")),
            "recommended_action": str(t.get("solution_applied", "限期整改")),
        }
        for i, t in enumerate(tickets[:4])
    ]
    out = dispatch_handle(_env("audit.remedy.remedy-dispatch", final_issue_set={"findings": findings}))
    assert out["summary"]["task_count"] == 4
    assert all(t["status"] == "not_started" for t in out["nodes"])
    assert out["nodes"][0]["severity"] == "high"
    print(f"[batch9-ok] dispatch tasks=4 tickets_src={src}")


def test_plan_review_approves_when_owners_assigned(_real_tickets) -> None:
    tickets, src = _real_tickets
    nodes = [
        {"task_id": f"RT-{i + 1:04d}", "name": f"整改-{t.get('fault_description', '')}",
         "owner": str(t.get("resolved_by") or ""), "due_date": "2026-10-01", "status": "not_started"}
        for i, t in enumerate(tickets[:3])
    ]
    ok = plan_review_handle(_env("audit.remedy.remedy-plan-review", remedy_task={
        "workflow_id": "remedy-2026", "nodes": nodes}))
    assert ok["summary"]["approved"] is True
    assert ok["summary"]["task_count"] == 3

    nodes[1]["owner"] = ""
    rej = plan_review_handle(_env("audit.remedy.remedy-plan-review", remedy_task={
        "workflow_id": "remedy-2026", "nodes": nodes}))
    assert rej["summary"]["approved"] is False
    assert "RT-0002" in rej["summary"]["missing_owner"]
    print(f"[batch9-ok] plan-review approve=1 reject=1 tickets_src={src}")


def test_overdue_alert_flags_overdue_tasks() -> None:
    progress = {
        "contract_id": "remedy-progress", "contract_version": "1.0.0",
        "as_of": "2026-09-10",
        "summary": {"total": 2, "done": 1, "in_progress": 0, "overdue": 1, "completion_rate": 0.5},
        "overdue_tasks": [
            {"task_id": "RT-0002", "name": "追责处理", "owner": "赵六", "overdue_days": 12},
        ],
        "series": [],
    }
    out = overdue_alert_handle(_env("audit.remedy.remedy-overdue-alert", remedy_progress=progress))
    assert out["summary"]["overdue_count"] == 1
    assert out["alerts"][0]["task_id"] == "RT-0002"
    assert out["alerts"][0]["overdue_days"] == 12
    print("[batch9-ok] overdue-alert flags=1")


def test_effect_verify_validates_evidence(_real_tickets) -> None:
    tickets, src = _real_tickets
    evidence = {
        "evidence": [
            {"evidence_id": "EV-1", "valid": True, "source": str(t.get("solution_applied", ""))}
            for t in tickets[:3]
        ]
    }
    ok = effect_verify_handle(_env("audit.remedy.remedy-effect-verify",
                                   remedy_evidence=evidence,
                                   evidence_index={"contract_id": "evidence-lineage", "nodes": [], "edges": []}))
    assert ok["summary"]["valid"] is True

    bad = {"evidence": [{"evidence_id": "EV-1", "valid": False}]}
    rej = effect_verify_handle(_env("audit.remedy.remedy-effect-verify",
                                    remedy_evidence=bad,
                                    evidence_index={"contract_id": "evidence-lineage", "nodes": [], "edges": []}))
    assert rej["summary"]["valid"] is False

    # document-content wrapped evidence (as produced by real chain seeds) must parse too
    wrapped = {"contract_id": "document-content", "contract_version": "1.0.0",
               "artifact": {"evidence": [{"evidence_id": "EV-9", "valid": True, "source": "补录凭证"}]}}
    ok_wrapped = effect_verify_handle(_env("audit.remedy.remedy-effect-verify",
                                           remedy_evidence=wrapped,
                                           evidence_index={"contract_id": "evidence-lineage", "nodes": [], "edges": []}))
    assert ok_wrapped["summary"]["valid"] is True
    print(f"[batch9-ok] effect-verify valid=1 reject=1 wrapped=1 tickets_src={src}")


def test_close_and_publish_require_clean_verdict() -> None:
    clean = {"snapshot_sha256": "0" * 64, "summary": {"valid": True}, "checks": [], "violations": []}
    ledger = close_handle(_env("audit.remedy.remedy-close", remedy_verdict=clean))
    assert ledger["summary"]["closed"] is True
    assert ledger["nodes"][0]["status"] == "closed"

    notice = publish_handle(_env("audit.remedy.remedy-publish", remedy_ledger=ledger))
    assert notice["artifact"]["published"] is True
    assert "整改" in notice["artifact"]["title"]

    dirty = {"snapshot_sha256": "0" * 64, "summary": {"valid": False}, "checks": [], "violations": [{"check_id": "columns"}]}
    rej_ledger = close_handle(_env("audit.remedy.remedy-close", remedy_verdict=dirty))
    assert rej_ledger["summary"]["closed"] is False
    assert rej_ledger["nodes"][0]["status"] == "open"
    with pytest.raises(Exception):
        publish_handle(_env("audit.remedy.remedy-publish", remedy_ledger=rej_ledger))
    print("[batch9-ok] close+publish ok=1 gate=1")


def test_batch9_uses_real_data_source_when_available() -> None:
    """Hard constraint check: E:\\数据 real remedy sample must exist."""
    if REAL_FAULT_TICKETS.exists():
        tickets = json.loads(REAL_FAULT_TICKETS.read_text(encoding="utf-8"))
        assert len(tickets) >= 10
        print(f"[batch9-data] E:\\数据 available: {len(tickets)} real fault tickets")
    else:
        print("[batch9-data] E:\\数据 NOT available -> remedy tests used SIMULATED data (noted)")
        pytest.skip("E:\\数据 real audit-material library is not mounted; simulated data used (noted)")
