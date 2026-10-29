from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.ai_planner.assessment import assess_candidate
from packages.ai_planner.audit_brain import audit_brief_issues, knowledge_hash
from packages.ai_planner.composer import PluginSpec, PortSpec
from packages.ai_planner.evidence import (
    AuditTaskBrief,
    CandidateDraft,
    EvidenceItem,
    PlanningRequest,
    ReviewResult,
    evidence_hash,
    fingerprint,
)


def scenario():
    request = PlanningRequest.model_validate(
        {
            "goal": "检查采购支出异常",
            "reason": "合成审计主脑测试",
            "idempotency_key": "brain-test-001",
            "requirements": [
                {"key": "anomaly", "description": "产出异常线索", "output_ports": ["anomaly-set"]}
            ],
        }
    )
    knowledge = EvidenceItem(
        evidence_id="knowledge-method",
        tenant_id=uuid4(),
        source_kind="knowledge",
        source_ref="db://fixture/method",
        version="1",
        sha256="a" * 64,
        content="采购审计方法与证据要求",
        origin_refs=["fixture:method"],
    )
    brief = {
        "goal_hash": fingerprint(request),
        "knowledge_hash": knowledge_hash([knowledge]),
        "objective": "识别采购异常风险线索",
        "scope": "仅对给定合成资料提出审计方案，不形成审计结论",
        "risk_hypotheses": ["重复或异常支付"],
        "procedures": [
            {
                "procedure_id": "check-payments",
                "requirement_keys": ["anomaly"],
                "objective": "识别支付风险",
                "method": "核对支付与凭证",
                "required_evidence": ["支付明细与凭证对应关系"],
                "acceptance_criteria": ["方案能追溯每项异常到来源凭证"],
                "knowledge_evidence_ids": [knowledge.evidence_id],
            }
        ],
        "assumptions": ["当前只验证静态方案"],
        "blockers": [],
        "followup_queries": [],
    }
    return request, [knowledge], brief


def test_audit_task_brief_carries_business_procedures_before_plugins():
    request, evidence, data = scenario()
    brief = AuditTaskBrief.model_validate(data)
    assert audit_brief_issues(request, brief, evidence) == []
    assert "plugin_id" not in brief.model_dump_json()


@pytest.mark.parametrize(
    "change",
    [
        "goal",
        "knowledge",
        "unknown_evidence",
        "history_as_method",
        "missing_goal",
        "unknown_goal",
        "duplicate_procedure",
        "empty_ready",
    ],
)
def test_invalid_handoff_cannot_become_an_audit_task(change):
    request, evidence, data = scenario()
    if change == "goal":
        data["goal_hash"] = "b" * 64
    if change == "knowledge":
        data["knowledge_hash"] = "c" * 64
    if change == "unknown_evidence":
        data["procedures"][0]["knowledge_evidence_ids"] = ["invented"]
    if change == "history_as_method":
        evidence[0] = evidence[0].model_copy(update={"source_kind": "history"})
    if change == "missing_goal":
        data["procedures"][0]["requirement_keys"] = []
    if change == "unknown_goal":
        data["procedures"][0]["requirement_keys"] = ["invented"]
    if change == "duplicate_procedure":
        data["procedures"].append(deepcopy(data["procedures"][0]))
    if change == "empty_ready":
        data["procedures"] = []
    assert audit_brief_issues(request, AuditTaskBrief.model_validate(data), evidence)


def test_blocked_brain_can_request_knowledge_without_fabricating_procedures():
    request, evidence, data = scenario()
    data.update(
        procedures=[],
        blockers=["缺完整适用依据"],
        followup_queries=[
            {"source_kind": "knowledge", "query": "适用依据正文", "purpose": "核验方法"}
        ],
    )
    assert audit_brief_issues(request, AuditTaskBrief.model_validate(data), evidence) == []


def test_brain_cannot_use_plugin_graph_queries_to_resolve_business_basis():
    request, evidence, data = scenario()
    data["followup_queries"] = [
        {"source_kind": "graph", "query": "plugins", "purpose": "jump to execution"}
    ]
    assert audit_brief_issues(request, AuditTaskBrief.model_validate(data), evidence)


def assessment_case():
    request, evidence, data = scenario()
    brief = AuditTaskBrief.model_validate(data)
    candidate = CandidateDraft.model_validate(
        {
            "nodes": [{"node_id": "check", "plugin_id": "fixture.check"}],
            "evidence_ids": [evidence[0].evidence_id],
            "decision_evidence": {"check": [evidence[0].evidence_id]},
            "procedure_nodes": {"check-payments": ["check"]},
            "rationale": "实现主脑要求的审计程序",
        }
    )
    specs = {
        "fixture.check": PluginSpec(
            "fixture.check",
            "audit.check",
            "check",
            "check",
            "verified",
            ("audit",),
            (),
            (PortSpec("anomaly-set", "artifact-ref.schema.json", "output"),),
        )
    }
    review = ReviewResult.model_validate(
        {
            "goal_hash": fingerprint(request),
            "candidate_hash": fingerprint(candidate),
            "evidence_hash": evidence_hash(evidence),
            "requirements": [
                {
                    "key": "anomaly",
                    "met": True,
                    "evidence_ids": [evidence[0].evidence_id],
                    "explanation": "目标覆盖",
                }
            ],
            "audit_brief_hash": fingerprint(brief),
            "procedures": [
                {
                    "procedure_id": "check-payments",
                    "met": True,
                    "evidence_ids": [evidence[0].evidence_id],
                    "explanation": "方案包含证据获取与追溯程序",
                }
            ],
            "summary": "方案通过",
        }
    )
    return request, evidence, brief, candidate, specs, review


def test_candidate_and_review_are_bound_to_the_audit_procedures():
    request, evidence, brief, candidate, specs, review = assessment_case()
    assessed = assess_candidate(request, candidate, evidence, review, specs, [], audit_brief=brief)
    assert assessed.passed, assessed
    assert assessed.audit_brief_hash == fingerprint(brief)
    assert assessed.plan["parameters"]["audit_brief_hash"] == fingerprint(brief)


@pytest.mark.parametrize(
    "change",
    [
        "missing_mapping",
        "unknown_procedure",
        "unknown_node",
        "stale_brief_review",
        "missing_procedure_review",
        "procedure_unmet",
        "invalid_procedure_reference",
        "return_to_brain",
        "blocked_brief",
    ],
)
def test_plugin_compilation_does_not_replace_audit_program_acceptance(change):
    request, evidence, brief, candidate, specs, review = assessment_case()
    if change == "missing_mapping":
        candidate = candidate.model_copy(update={"procedure_nodes": {}})
    if change == "unknown_procedure":
        candidate = candidate.model_copy(update={"procedure_nodes": {"wrong": ["check"]}})
    if change == "unknown_node":
        candidate = candidate.model_copy(update={"procedure_nodes": {"check-payments": ["wrong"]}})
    if change == "stale_brief_review":
        review = review.model_copy(update={"audit_brief_hash": "0" * 64})
    if change == "missing_procedure_review":
        review = review.model_copy(update={"procedures": []})
    if change == "procedure_unmet":
        review = review.model_copy(
            update={"procedures": [review.procedures[0].model_copy(update={"met": False})]}
        )
    if change == "invalid_procedure_reference":
        review = review.model_copy(
            update={
                "procedures": [
                    review.procedures[0].model_copy(update={"evidence_ids": ["invented"]})
                ]
            }
        )
    if change == "return_to_brain":
        review = review.model_copy(update={"return_to_audit_brain": True})
    if change == "blocked_brief":
        brief = brief.model_copy(update={"blockers": ["缺准则依据"]})
    review = review.model_copy(update={"candidate_hash": fingerprint(candidate)})
    assert not assess_candidate(
        request, candidate, evidence, review, specs, [], audit_brief=brief
    ).passed


def test_compiled_identity_changes_when_audit_method_changes():
    request, evidence, brief, candidate, specs, review = assessment_case()
    old = assess_candidate(request, candidate, evidence, review, specs, [], audit_brief=brief)
    updated = brief.model_copy(update={"scope": "修订范围但保留用户目标"})
    new = assess_candidate(
        request,
        candidate,
        evidence,
        review.model_copy(update={"audit_brief_hash": fingerprint(updated)}),
        specs,
        [],
        audit_brief=updated,
    )
    assert old.passed and new.passed
    assert old.compiled_plan_hash != new.compiled_plan_hash


@pytest.mark.parametrize("field", ["risk_hypotheses", "required_evidence", "acceptance_criteria", "objective", "scope", "method"])
def test_brief_cannot_hide_missing_business_criteria_in_empty_strings(field):
    _, _, data = scenario()
    if field in {"objective", "scope"}:
        data[field] = " "
    elif field == "method":
        data["procedures"][0][field] = " "
    elif field == "risk_hypotheses":
        data[field] = [" "]
    else:
        data["procedures"][0][field] = [" "]
    with pytest.raises(ValidationError):
        AuditTaskBrief.model_validate(data)
