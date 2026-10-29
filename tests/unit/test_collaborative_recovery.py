"""Recovery and negative-source boundaries of the persistent planning loop."""

from __future__ import annotations

import importlib.util
import json
import math
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from packages.ai_planner.collaborative import CollaborativePlanner
from packages.ai_planner.composer import PortSpec
from packages.ai_planner.evidence import (
    CandidateDraft,
    EvidenceItem,
    PlanningInput,
    ResolvedInput,
    ReviewResult,
    SourceResult,
    fingerprint,
)

_helper_spec = importlib.util.spec_from_file_location(
    "collaborative_loop_test_helpers", Path(__file__).with_name("test_collaborative_loop.py"),
)
assert _helper_spec is not None and _helper_spec.loader is not None
helpers = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(helpers)


class _Sources(helpers.Sources):
    def __init__(self, context):
        super().__init__(context)
        self.resolve_calls = 0
        self.validation_calls = 0
        self.invalid_evidence = False
        self.input_sha = "b" * 64

    def resolve_inputs(self, context, inputs):
        self.resolve_calls += 1
        return [ResolvedInput(
            port_id=item.port_id, schema_ref=item.schema_ref, artifact_id=item.artifact_id,
            sha256=self.input_sha, available=True,
        ) for item in inputs]

    def validate_evidence(self, context, items):
        self.validation_calls += 1
        return ["source version changed"] if self.invalid_evidence else []


class _OneRoundPlanner:
    """A valid fixed proposal isolates recovery from iterative fixture choices."""

    def plan(self, context, complete_json):
        evidence_ids = [item["evidence_id"] for item in context["evidence"]]
        return CandidateDraft.model_validate({
            "nodes": [{"node_id": "final", "plugin_id": "x.final"}],
            "evidence_ids": evidence_ids, "decision_evidence": {"final": evidence_ids},
            "rationale": "fixed, source-cited recovery fixture",
            "procedure_nodes": {"p1": ["final"]},
        })


class _ApprovingReviewer:
    def review(self, context, complete_json):
        evidence_ids = [item["evidence_id"] for item in context["evidence"]]
        return ReviewResult.model_validate({
            "goal_hash": context["goal_hash"], "candidate_hash": context["candidate_hash"],
            "evidence_hash": context["evidence_hash"],
            "requirements": [{
                "key": item["key"], "met": True, "evidence_ids": evidence_ids,
                "explanation": "The fixed recovery proposal provides the requested fixture output.",
            } for item in context["request"]["requirements"]],
            "blockers": [], "followup_queries": [], "summary": "fixed recovery fixture approved",
            "audit_brief_hash": context.get("audit_brief_hash"),
            "procedures": [{"procedure_id": "p1", "met": True, "evidence_ids": evidence_ids, "explanation": "fixed recovery procedure covered"}],
        })


def _engine(context, specs, *, sources=None, clock=None, forbid_roles=False):
    events = []
    planner = Mock() if forbid_roles else _OneRoundPlanner()
    reviewer = Mock() if forbid_roles else _ApprovingReviewer()
    if forbid_roles:
        planner.plan.side_effect = AssertionError("recovery must not call the planner again")
        reviewer.review.side_effect = AssertionError("recovery must not call the reviewer again")
    kwargs: dict[str, Any] = {"clock": clock} if clock else {}
    engine = CollaborativePlanner(
        sources=sources or _Sources(context), gateway=lambda _cap, _args: str(uuid4()),
        model=None, planner=planner, reviewer=reviewer, brain=helpers.Brain(), specs=specs,
        emit=lambda key, kind, payload, checkpoint: events.append((key, kind, payload, checkpoint)),
        **kwargs,
    )
    return engine, events, planner, reviewer


def _accepted_checkpoint(*, with_input=False):
    context, request, specs = helpers.scenario()
    request = request.model_copy(update={"budget": request.budget.model_copy(update={"max_rounds": 1})})
    if with_input:
        item = PlanningInput(port_id="raw", schema_ref="artifact-ref.schema.json", artifact_id=uuid4())
        request = request.model_copy(update={"inputs": [item]})
        specs = {"x.final": replace(specs["x.final"], inputs=(PortSpec("raw", item.schema_ref, "input"),))}
    engine, events, _, _ = _engine(context, specs)
    result = engine.run(context, request)
    assert result["status"] == "accepted", result
    # The terminal response has not been saved yet in this crash window.
    checkpoint = deepcopy(next(event[3] for event in events if event[1] == "round_finished"))
    assert checkpoint["round_no"] == 2 and checkpoint["assessment"]["passed"] is True
    return context, request, specs, checkpoint


@pytest.mark.parametrize("change", ["missing", "edited"])
def test_saved_acceptance_cannot_reuse_a_missing_or_changed_audit_program(change):
    context, request, specs, checkpoint = _accepted_checkpoint()
    if change == "missing":
        checkpoint.pop("audit_brief")
    else:
        checkpoint["audit_brief"]["scope"] = "different audit scope"
    engine, _, planner, reviewer = _engine(context, specs, forbid_roles=True)
    result = engine.run(context, request, checkpoint=checkpoint)
    assert result["status"] == "failed"
    assert result["assessment"]["passed"] is False
    assert result["assessment"]["plan"] is None
    planner.plan.assert_not_called()
    reviewer.review.assert_not_called()


def test_a_passed_round_checkpoint_resumes_after_fresh_verification_without_new_roles() -> None:
    context, request, specs, checkpoint = _accepted_checkpoint(with_input=True)
    sources = _Sources(context)
    engine, _, planner, reviewer = _engine(context, specs, sources=sources, forbid_roles=True)

    result = engine.run(context, request, checkpoint=checkpoint)

    assert result["status"] == "accepted", result
    assert sources.resolve_calls >= 2
    assert sources.validation_calls == 1
    assert sources.calls == []
    planner.plan.assert_not_called()
    reviewer.review.assert_not_called()
    assert result["model_calls"] == checkpoint["model_calls"]
    assert result["assessment"]["goal_hash"] == fingerprint(request)


@pytest.mark.parametrize("change", ["source_version", "input_snapshot"])
def test_recovery_does_not_reuse_qualification_after_a_source_or_input_change(change: str) -> None:
    context, request, specs, checkpoint = _accepted_checkpoint(with_input=True)
    sources = _Sources(context)
    if change == "source_version":
        sources.invalid_evidence = True
    else:
        sources.input_sha = "c" * 64
    engine, _, planner, reviewer = _engine(context, specs, sources=sources, forbid_roles=True)

    result = engine.run(context, request, checkpoint=checkpoint)

    assert result["status"] != "accepted", result
    assessment = result["assessment"]
    assert assessment is None or (assessment["passed"] is False and assessment["plan"] is None)
    planner.plan.assert_not_called()
    reviewer.review.assert_not_called()


@pytest.mark.parametrize("status", ["denied", "error", "degraded", "empty"])
def test_failed_source_items_never_reach_evidence_checkpoints_events_or_roles(status: str) -> None:
    context, request, specs = helpers.scenario()
    secret = "sensitive content from a source that did not authorize a result"
    sources = _Sources(context)

    def failed_query(ctx, query):
        return SourceResult(
            source_kind=query.source_kind, status=status, detail="source did not provide an accepted result",
            items=[EvidenceItem(
                evidence_id="must-not-leak", tenant_id=ctx.tenant_id,
                source_kind=query.source_kind, source_ref="local://restricted", version="1",
                sha256="a" * 64, content=secret, origin_refs=["restricted:one"],
            )],
        )

    sources.query = failed_query
    engine, events, planner, reviewer = _engine(context, specs, sources=sources, forbid_roles=True)
    result = engine.run(context, request)

    assert result["status"] in {"blocked_policy", "failed"}
    assert result["evidence"] == []
    assert secret not in json.dumps({"result": result, "events": events}, ensure_ascii=False)
    assert "must-not-leak" not in json.dumps({"result": result, "events": events}, ensure_ascii=False)
    planner.plan.assert_not_called()
    reviewer.review.assert_not_called()


@pytest.mark.parametrize("slow_operation", ["evidence_verification", "input_verification"])
def test_elapsed_budget_is_rechecked_after_final_verification(slow_operation: str) -> None:
    context, request, specs = helpers.scenario()
    clock = Mock(return_value=0.0)
    sources = _Sources(context)
    original_inputs = sources.resolve_inputs

    def validate(ctx, items):
        sources.validation_calls += 1
        if slow_operation == "evidence_verification":
            clock.return_value = float(request.budget.max_seconds + 1)
        return []

    def resolve(ctx, inputs):
        result = original_inputs(ctx, inputs)
        if slow_operation == "input_verification" and sources.resolve_calls > 1:
            clock.return_value = float(request.budget.max_seconds + 1)
        return result

    sources.validate_evidence = validate
    sources.resolve_inputs = resolve
    engine, _, _, _ = _engine(context, specs, sources=sources, clock=clock)
    result = engine.run(context, request)

    assert result["status"] == "budget_exhausted", result
    assert result["assessment"] is None or result["assessment"]["passed"] is False


@pytest.mark.parametrize("field", ["tenant_id", "session_id", "request_hash", "missing"])
def test_checkpoint_identity_is_verified_before_any_access_or_role_call(field: str) -> None:
    context, request, specs, checkpoint = _accepted_checkpoint()
    if field == "missing":
        checkpoint.pop("identity")
    else:
        checkpoint["identity"][field] = str(uuid4()) if field != "request_hash" else "0" * 64
    sources = _Sources(context)
    engine, _, planner, reviewer = _engine(context, specs, sources=sources, forbid_roles=True)
    result = engine.run(context, request, checkpoint=checkpoint)

    assert result["status"] == "failed", result
    assert "checkpoint" in result["detail"]
    assert sources.resolve_calls == 0 and sources.calls == []
    planner.plan.assert_not_called()
    reviewer.review.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("query_count", -1), ("model_calls", -1), ("round_no", -1), ("round_no", 0),
    ("query_count", True), ("model_calls", False), ("query_count", 1000),
    ("elapsed_seconds", -1), ("elapsed_seconds", float("nan")),
    ("elapsed_seconds", float("inf")),
])
def test_checkpoint_counters_and_elapsed_time_cannot_disable_the_budget(field: str, value: Any) -> None:
    context, request, specs, checkpoint = _accepted_checkpoint()
    checkpoint[field] = value
    sources = _Sources(context)
    engine, _, planner, reviewer = _engine(context, specs, sources=sources, forbid_roles=True)
    result = engine.run(context, request, checkpoint=checkpoint)

    assert result["status"] == "failed", result
    assert "checkpoint" in result["detail"]
    assert sources.resolve_calls == 0 and sources.calls == []
    assert math.isfinite(result["checkpoint"]["elapsed_seconds"])
    planner.plan.assert_not_called()
    reviewer.review.assert_not_called()
