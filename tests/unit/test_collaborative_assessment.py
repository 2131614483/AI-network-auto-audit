"""Independent approval cannot be manufactured by a planning agent."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from packages.ai_planner import assessment as assessment_module
from packages.ai_planner.assessment import assess_candidate
from packages.ai_planner.composer import PluginSpec, PortSpec, schema_sha256
from packages.ai_planner.evidence import (
    CandidateDraft,
    EvidenceItem,
    GoalRequirement,
    PlanningBudget,
    PlanningInput,
    PlanningRequest,
    ResolvedInput,
    ReviewResult,
    evidence_hash,
    fingerprint,
)

SCHEMA = "artifact-ref.schema.json"


def _spec(plugin_id: str, capability: str, before: str, after: str) -> PluginSpec:
    return PluginSpec(
        plugin_id=plugin_id, capability=capability, name=plugin_id, description="local fixture",
        lifecycle="verified", domains=("audit",),
        inputs=(PortSpec(before, SCHEMA, "input"),),
        outputs=(PortSpec(after, SCHEMA, "output"),),
    )


@pytest.fixture()
def example():
    artifact_id, tenant_id = uuid4(), uuid4()
    request = PlanningRequest(
        goal="prepare the report", reason="local test", idempotency_key="assessment-fixture",
        requirements=[GoalRequirement(key="report", description="A report is required", capabilities=["audit.demo.report"], output_ports=["report"])],
        inputs=[PlanningInput(port_id="raw", schema_ref=SCHEMA, artifact_id=artifact_id)],
    )
    specs = {
        "demo.ingest": _spec("demo.ingest", "audit.demo.ingest", "raw", "middle"),
        "demo.report": _spec("demo.report", "audit.demo.report", "middle", "report"),
    }
    evidence = [EvidenceItem(
        evidence_id=f"{source}-1", tenant_id=tenant_id, source_kind=source,
        source_ref=f"{source}://local-1", version="1", sha256="a" * 64,
        content="source material", origin_refs=[f"origin:{source}"],
    ) for source in ("graph", "history", "knowledge")]
    candidate = CandidateDraft.model_validate({
        "nodes": [{"node_id": "ingest", "plugin_id": "demo.ingest"}, {"node_id": "report", "plugin_id": "demo.report"}],
        "edges": [{"source_node": "ingest", "source_port": "middle", "target_node": "report", "target_port": "middle"}],
        "evidence_ids": [item.evidence_id for item in evidence], "rationale": "source-grounded choice",
        "decision_evidence": {"ingest": ["graph-1"], "report": ["knowledge-1"]},
    })
    resolved = [ResolvedInput(port_id="raw", schema_ref=SCHEMA, artifact_id=artifact_id, sha256="b" * 64, available=True)]
    return request, candidate, evidence, specs, resolved


def _review(request: PlanningRequest, candidate: CandidateDraft, evidence: list[EvidenceItem], **updates) -> ReviewResult:
    data = {
        "goal_hash": fingerprint(request), "candidate_hash": fingerprint(candidate), "evidence_hash": evidence_hash(evidence),
        "requirements": [{"key": "report", "met": True, "evidence_ids": ["knowledge-1"], "explanation": "The source supports this requirement."}],
        "summary": "independent review", **updates,
    }
    return ReviewResult.model_validate(data)


def _assess(example, **updates):
    request, candidate, evidence, specs, resolved = example
    data = {"request": request, "candidate": candidate, "evidence": evidence,
            "review": _review(request, candidate, evidence), "specs": specs, "resolved_inputs": resolved}
    data.update(updates)
    return assess_candidate(**data)


def test_assessment_expands_authoritative_contracts_and_compiles(example) -> None:
    result = _assess(example)
    assert result.passed is True, result
    assert result.hard_issues == [] and result.reviewer_issues == []
    assert result.plan is not None and result.compiled_plan_hash
    assert result.plan["nodes"][0]["input_ports"][0]["schema_sha256"] == schema_sha256(SCHEMA)
    assert result.plan["seed_inputs"] == [["ingest", "raw"]]
    assert result.plan["budget"]["max_chain_length"] == example[0].budget.max_nodes


def test_unknown_plugin_cannot_borrow_a_registered_capability(example) -> None:
    raw = example[1].model_dump(mode="json")
    raw["nodes"][0]["plugin_id"] = "invented.plugin"
    result = _assess(example, candidate=CandidateDraft.model_validate(raw))
    assert not result.passed
    assert any("plugin" in issue for issue in result.hard_issues)


def test_spec_map_identity_must_match_the_requested_plugin(example) -> None:
    specs = dict(example[3])
    specs["demo.ingest"] = replace(specs["demo.ingest"], plugin_id="another.plugin")
    result = _assess(example, specs=specs)
    assert not result.passed
    assert any("binding" in issue for issue in result.hard_issues)


def test_a_model_cannot_add_an_undeclared_parameter(example) -> None:
    raw = example[1].model_dump(mode="json")
    raw["nodes"][0]["parameters"] = {"execute_command": "not-permitted"}
    result = _assess(example, candidate=CandidateDraft.model_validate(raw))
    assert not result.passed
    assert any("parameter" in issue for issue in result.hard_issues)


def test_only_registered_parameter_keys_can_be_selected(example, monkeypatch) -> None:
    monkeypatch.setattr("packages.ai_planner.assessment.capability_parameters", lambda _cap: {"limit": 5})
    raw = example[1].model_dump(mode="json")
    raw["nodes"][0]["parameters"] = {"limit": 3}
    candidate = CandidateDraft.model_validate(raw)
    result = _assess(example, candidate=candidate, review=_review(example[0], candidate, example[2]))
    assert result.passed, result
    assert result.plan["nodes"][0]["parameters"] == {"limit": 3}


@pytest.mark.parametrize("declared,value,allowed", [
    (False, True, True), (False, 1, False), (10, True, False), (10, "10", False),
    (10, 2.5, True), (1.5, 2, True), ("scope", {}, False), ("scope", "another", True),
    ([1], {"first": 1}, False), ([1], [], True), ({"a": 1}, [], False),
    ({"a": 1}, {"b": 2}, True), (None, False, False), (None, None, True),
])
def test_declared_parameter_types_are_preserved_without_inventing_ranges(
    example, monkeypatch, declared, value, allowed: bool,
) -> None:
    monkeypatch.setattr("packages.ai_planner.assessment.capability_parameters", lambda _cap: {"limit": declared})
    raw = example[1].model_dump(mode="json")
    raw["nodes"][0]["parameters"] = {"limit": value}
    candidate = CandidateDraft.model_validate(raw)
    result = _assess(example, candidate=candidate, review=_review(example[0], candidate, example[2]))
    assert result.passed is allowed, result
    if not allowed:
        assert any("parameter_type" in issue for issue in result.hard_issues)


def test_each_node_needs_specific_evidence_already_cited_by_the_candidate(example) -> None:
    candidate = example[1].model_copy(update={"decision_evidence": {"ingest": ["graph-1"]}})
    result = _assess(example, candidate=candidate, review=_review(example[0], candidate, example[2]))
    assert not result.passed
    assert any("decision_evidence" in issue for issue in result.hard_issues)


@pytest.mark.parametrize("change", ["missing", "unavailable", "wrong_schema", "wrong_artifact"])
def test_required_seed_must_be_available_and_authorized(example, change: str) -> None:
    resolved = list(example[4])
    if change == "missing":
        resolved = []
    elif change == "unavailable":
        resolved = [resolved[0].model_copy(update={"available": False})]
    elif change == "wrong_schema":
        resolved = [resolved[0].model_copy(update={"schema_ref": "another-schema"})]
    else:
        resolved = [resolved[0].model_copy(update={"artifact_id": uuid4()})]
    result = _assess(example, resolved_inputs=resolved)
    assert not result.passed
    assert any("input" in issue for issue in result.hard_issues)


def test_node_budget_is_checked_before_compilation(example) -> None:
    request = example[0].model_copy(update={"budget": PlanningBudget(max_nodes=1)})
    result = _assess(example, request=request)
    assert not result.passed
    assert any("budget" in issue for issue in result.hard_issues)


def test_structurally_valid_but_uncovered_goal_is_rejected(example) -> None:
    request = example[0].model_copy(update={"requirements": [GoalRequirement(key="report", description="Different output", capabilities=["audit.missing.check"], output_ports=["missing-output"])]})
    result = _assess(example, request=request)
    assert not result.passed
    assert any("requirement" in issue for issue in result.hard_issues)


def test_same_schema_does_not_authorize_unrelated_business_ports(example) -> None:
    specs = dict(example[3])
    specs["demo.report"] = replace(specs["demo.report"], inputs=(PortSpec("unrelated", SCHEMA, "input"),))
    raw = example[1].model_dump(mode="json")
    raw["edges"][0]["target_port"] = "unrelated"
    candidate = CandidateDraft.model_validate(raw)
    result = _assess(example, candidate=candidate, review=_review(example[0], candidate, example[2]), specs=specs)
    assert not result.passed
    assert any("semantic" in issue for issue in result.hard_issues)


def test_unregistered_adapter_is_rejected(example) -> None:
    raw = example[1].model_dump(mode="json")
    raw["edges"][0]["adapter"] = "invented-adapter"
    candidate = CandidateDraft.model_validate(raw)
    result = _assess(example, candidate=candidate, review=_review(example[0], candidate, example[2]))
    assert not result.passed
    assert any("adapter" in issue for issue in result.hard_issues)


def test_reviewed_adapter_cannot_convert_unrelated_schema_pairs(example) -> None:
    raw = example[1].model_dump(mode="json")
    # This adapter is registered, but neither side is its candidates/backtest contract.
    raw["edges"][0]["adapter"] = "candidates-to-backtest"
    candidate = CandidateDraft.model_validate(raw)
    result = _assess(example, candidate=candidate, review=_review(example[0], candidate, example[2]))
    assert not result.passed
    assert any("adapter_contract" in issue for issue in result.hard_issues)


def test_adapter_without_reviewed_conversion_metadata_is_rejected(example, monkeypatch) -> None:
    monkeypatch.setitem(assessment_module.ADAPTERS, "runtime-only-adapter", lambda *_args: {})
    raw = example[1].model_dump(mode="json")
    raw["edges"][0]["adapter"] = "runtime-only-adapter"
    candidate = CandidateDraft.model_validate(raw)
    result = _assess(example, candidate=candidate, review=_review(example[0], candidate, example[2]))
    assert not result.passed
    assert any("adapter_contract" in issue for issue in result.hard_issues)


@pytest.mark.parametrize("suffix", ["", ".schema.json"])
def test_reviewed_adapter_accepts_only_its_registered_schema_pair(example, suffix: str) -> None:
    specs = dict(example[3])
    specs["demo.ingest"] = replace(specs["demo.ingest"], outputs=(PortSpec(
        "middle", f"audit-quality-candidates{suffix}", "output",
    ),))
    specs["demo.report"] = replace(specs["demo.report"], inputs=(PortSpec(
        "middle", f"backtest-report{suffix}", "input",
    ),))
    raw = example[1].model_dump(mode="json")
    raw["edges"][0]["adapter"] = "candidates-to-backtest"
    candidate = CandidateDraft.model_validate(raw)
    result = _assess(example, specs=specs, candidate=candidate, review=_review(example[0], candidate, example[2]))
    assert result.passed, result


def test_compile_errors_cannot_be_overruled_by_a_positive_review(example) -> None:
    raw = example[1].model_dump(mode="json")
    raw["edges"] = []
    candidate = CandidateDraft.model_validate(raw)
    result = _assess(example, candidate=candidate, review=_review(example[0], candidate, example[2]))
    assert not result.passed
    assert any("input" in issue for issue in result.hard_issues)
    assert result.compiled_plan_hash is None


@pytest.mark.parametrize("change", ["goal_hash", "candidate_hash", "evidence_hash", "missing_review", "duplicate_review", "not_met", "unknown_citation", "uncited_candidate_evidence"])
def test_review_is_bound_to_current_candidate_evidence_and_every_requirement(example, change: str) -> None:
    raw = _review(example[0], example[1], example[2]).model_dump(mode="json")
    candidate = example[1]
    if change in {"goal_hash", "candidate_hash", "evidence_hash"}:
        raw[change] = "0" * 64
    elif change == "missing_review":
        raw["requirements"] = []
    elif change == "duplicate_review":
        raw["requirements"] *= 2
    elif change == "not_met":
        raw["requirements"][0]["met"] = False
    elif change == "unknown_citation":
        raw["requirements"][0]["evidence_ids"] = ["does-not-exist"]
    else:
        candidate = candidate.model_copy(update={"evidence_ids": ["graph-1"]})
        raw["candidate_hash"] = fingerprint(candidate)
    result = _assess(example, candidate=candidate, review=ReviewResult.model_validate(raw))
    assert not result.passed
    assert result.reviewer_issues


def test_reviewer_blockers_are_preserved_and_prevent_qualification(example) -> None:
    review = _review(example[0], example[1], example[2], blockers=["Missing independent support"])
    result = _assess(example, review=review)
    assert not result.passed
    assert "Missing independent support" in result.reviewer_issues


def test_an_old_review_cannot_approve_a_changed_goal_with_the_same_requirement_key(example) -> None:
    request = example[0].model_copy(update={"goal": "a different business goal"})
    result = _assess(example, request=request)
    assert not result.passed
    assert any("stale_goal_review" in issue for issue in result.reviewer_issues)


def test_candidate_citations_must_exist_and_cannot_mix_tenants(example) -> None:
    candidate = example[1].model_copy(update={"evidence_ids": ["invented"]})
    result = _assess(example, candidate=candidate, review=_review(example[0], candidate, example[2]))
    assert not result.passed
    assert any("evidence" in issue for issue in result.hard_issues)
    items = list(example[2])
    items[1] = items[1].model_copy(update={"tenant_id": uuid4()})
    mixed = _assess(example, evidence=items, review=_review(example[0], example[1], items))
    assert not mixed.passed
    assert any("tenant" in issue for issue in mixed.hard_issues)


def test_assessment_hashes_change_with_the_goal_and_evidence(example) -> None:
    baseline = _assess(example)
    request = example[0].model_copy(update={"goal": "same contract, changed business goal"})
    changed_goal = _assess(example, request=request)
    assert changed_goal.goal_hash != baseline.goal_hash
    items = list(example[2])
    items[1] = items[1].model_copy(update={"version": "2"})
    changed_evidence = _assess(example, evidence=items, review=_review(example[0], example[1], items))
    assert changed_evidence.evidence_hash != baseline.evidence_hash


def test_resolved_input_snapshot_is_bound_into_the_compiled_plan_hash(example) -> None:
    baseline = _assess(example)
    changed = [example[4][0].model_copy(update={"sha256": "c" * 64})]
    result = _assess(example, resolved_inputs=changed)
    assert result.passed, result
    assert baseline.plan["parameters"]["input_snapshot_hash"] != result.plan["parameters"]["input_snapshot_hash"]
    assert baseline.compiled_plan_hash != result.compiled_plan_hash
