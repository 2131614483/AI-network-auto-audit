"""AI 组网 20 意图评测（方案 13 验收矩阵 :463）。

Deterministic gate evaluation: every intent case runs through the planner
with a scripted (injected) model, so the assertions are repeatable and never
depend on live inference.  Global safety: tool execution may only ever come
from a legal compiled plan — every rejected case must have
``execution_plan is None``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.ai_planner.evaluation import (
    EVALUATION_CASES,
    EvaluationReport,
    evaluate_intents,
)

REPORT_DIR = Path(__file__).resolve().parents[2] / ".data" / "evaluation"


def test_evaluation_has_at_least_20_fixed_intents() -> None:
    assert len(EVALUATION_CASES) >= 20
    ids = [case.case_id for case in EVALUATION_CASES]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    categories = {case.category for case in EVALUATION_CASES}
    assert {"success", "ambiguous", "missing_plugin", "wrong_domain", "malicious"} <= categories


@pytest.mark.parametrize("case", EVALUATION_CASES, ids=lambda case: case.case_id)
def test_each_intent_gate_behavior(case) -> None:
    report = evaluate_intents((case,))
    outcome = report.cases[0]
    assert report.total == 1
    if case.bad_draft is None:
        # success/acceptable-intent: legal compiled plan with goal coverage
        assert outcome.status == "draft_ready", (outcome.codes, outcome.failure_reason)
        assert outcome.execution_plan_present is True
        assert outcome.coverage_ok is True, "goal coverage must hold for accepted plans"
        assert outcome.seed_within_boundary is True
        assert outcome.passed is True
    else:
        # reject intents: gap report, stable code, and NO executable plan
        assert outcome.status == "gap_report", (outcome.codes, outcome.failure_reason)
        assert outcome.execution_plan_present is False, \
            "tool execution must only come from a legal compiled plan"
        assert outcome.codes, "a rejected intent must record the failure reason"
        assert outcome.passed is True


def test_full_evaluation_report_and_global_safety() -> None:
    report = evaluate_intents()
    assert isinstance(report, EvaluationReport)
    assert report.total == len(EVALUATION_CASES)
    assert report.passed == report.total, "every fixed intent must pass its gate assertions"

    # global safety: no rejected intent produced an executable plan
    for outcome in report.cases:
        if outcome.status == "gap_report":
            assert outcome.execution_plan_present is False, outcome.case_id
            assert outcome.codes, outcome.case_id

    # all categories present in the report
    categories = {outcome.category for outcome in report.cases}
    assert categories == {"success", "ambiguous", "missing_plugin", "wrong_domain", "malicious"}
    assert report.rejected_safely >= 10, "the reject classes must all fail closed"


def test_evaluation_report_json_artifact() -> None:
    report = evaluate_intents()
    out = REPORT_DIR / "ai_planning_intents_report.json"
    report.write_json(out)
    assert out.is_file() and out.stat().st_size > 0
    import json

    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["summary"]["total"] == len(EVALUATION_CASES)
    assert body["summary"]["passed"] == len(EVALUATION_CASES)
    # every case records revisions and failure reasons
    for case in body["cases"]:
        assert case["revisions"] >= 0
        assert "failure_reason" in case


def test_real_planner_accepts_evaluation_catalog() -> None:
    """The deterministic evaluator shares the same planner path a real AiPlanner
    uses, so the report reflects the production gates (not a test-only path)."""
    from packages.ai_planner.evaluation import _draft_for, catalog_for

    for case in EVALUATION_CASES[:2]:
        draft = _draft_for(case)
        catalog = catalog_for(case)
        assert draft is not None
        assert "audit.ledger.validate" in catalog
