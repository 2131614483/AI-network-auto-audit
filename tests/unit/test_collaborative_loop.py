from __future__ import annotations

from uuid import uuid4

from packages.ai_planner.collaborative import CollaborativePlanner
from packages.ai_planner.composer import PluginSpec, PortSpec
from packages.ai_planner.evidence import (
    AuditTaskBrief,
    CandidateDraft,
    EvidenceItem,
    PlanningContext,
    PlanningRequest,
    ReviewResult,
    SourceResult,
    fingerprint,
)


def scenario():
    ctx = PlanningContext(tenant_id=uuid4(), trace_id=uuid4(), session_id=uuid4())
    request = PlanningRequest.model_validate({
        "goal": "生成抽样方案", "reason": "验证多轮组网", "idempotency_key": "loop-test-001",
        "requirements": [{"key": "target", "description": "产出方案", "output_ports": ["done"]}],
    })
    spec = PluginSpec("x.final", "audit.final", "final", "生成方案", "verified", ("audit",), (), (PortSpec("done", "artifact-ref.schema.json", "output"),))
    alternative = PluginSpec("x.default", "audit.default", "default", "默认方法", "verified", ("audit",), (), spec.outputs)
    return ctx, request, {spec.plugin_id: spec, alternative.plugin_id: alternative}


class Sources:
    def __init__(self, ctx):
        self.ctx = ctx
        self.calls = []

    def query(self, context, query):
        self.calls.append(query)
        text = "补充的适用依据" if "补充" in query.query else "初步参考"
        ev = EvidenceItem(
            evidence_id=f"{query.source_kind}-{len(self.calls)}", tenant_id=context.tenant_id,
            source_kind=query.source_kind, source_ref=f"{query.source_kind}://fixture",
            version="1", sha256=fingerprint(text), content=text,
            origin_refs=["fixture:one-origin"], authority="published",
        )
        return SourceResult(source_kind=query.source_kind, status="available", items=[ev])

    def resolve_inputs(self, context, inputs):
        return []

    def validate_evidence(self, context, items):
        return []


class Brain:
    def __init__(self):
        self.seen = []

    def build_brief(self, context, complete_json):
        self.seen.append(context)
        ids = [item["evidence_id"] for item in context["evidence"]]
        return AuditTaskBrief.model_validate({
            "goal_hash": context["goal_hash"], "knowledge_hash": context["knowledge_hash"],
            "objective": "按用户目标形成合成审计程序", "scope": "plan_only synthetic fixture", "risk_hypotheses": ["缺少目标产物或证据"],
            "procedures": [{"procedure_id": "p1", "requirement_keys": [item["key"] for item in context["request"]["requirements"]],
                "objective": "满足目标", "method": "按已检索的合成方法核验", "required_evidence": ["来源可追溯的输入和拟输出"],
                "acceptance_criteria": ["方案包含产出步骤和来源引用"], "knowledge_evidence_ids": ids}],
        })


class Planner:
    def __init__(self):
        self.seen = []

    def plan(self, context, complete_json):
        self.seen.append(context)
        ids = [row["evidence_id"] for row in context["evidence"]]
        selected = "x.final" if any("补充" in row["content"] for row in context["evidence"]) else "x.default"
        return CandidateDraft.model_validate({
            "nodes": [{"node_id": "final", "plugin_id": selected}],
            "evidence_ids": ids, "decision_evidence": {"final": ids},
            "procedure_nodes": {"p1": ["final"]},
            "rationale": "以新增依据修订" if context["round_no"] > 1 else "初稿待核验",
        })


class Reviewer:
    def review(self, context, complete_json):
        second = any("补充" in row["content"] for row in context["evidence"])
        ids = [row["evidence_id"] for row in context["evidence"]]
        return ReviewResult.model_validate({
            "goal_hash": context["goal_hash"],
            "candidate_hash": context["candidate_hash"], "evidence_hash": context["evidence_hash"],
            "requirements": [{"key": "target", "met": second, "evidence_ids": ids, "explanation": "核验依据"}],
            "blockers": [] if second else ["缺补充适用依据"],
            "followup_queries": [] if second else [{"source_kind": "knowledge", "query": "补充适用条件", "purpose": "解决阻断项"}],
            "summary": "通过" if second else "需补查",
            "audit_brief_hash": context.get("audit_brief_hash"),
            "procedures": [{"procedure_id": "p1", "met": second, "evidence_ids": ids, "explanation": "逐程序核验"}],
        })


def make_engine(ctx, sources=None, reviewer=None, gateway=None):
    _, _, specs = scenario()
    events = []
    planner = Planner()
    engine = CollaborativePlanner(
        sources=sources or Sources(ctx), gateway=gateway or (lambda capability, arguments: str(uuid4())),
        model=None, planner=planner, reviewer=reviewer or Reviewer(), brain=Brain(), specs=specs,
        emit=lambda key, kind, payload, checkpoint: events.append((key, kind, payload, checkpoint)),
    )
    return engine, events, planner


def test_review_drives_a_real_second_source_query_and_new_candidate() -> None:
    ctx, request, _ = scenario()
    engine, events, planner = make_engine(ctx)
    result = engine.run(ctx, request)

    assert result["status"] == "accepted", result
    assert len(engine.sources.calls) == 4
    assert {q.source_kind for q in engine.sources.calls[:3]} == {"graph", "history", "knowledge"}
    assert engine.sources.calls[-1].query == "补充适用条件"
    assert len(planner.seen[1]["evidence"]) > len(planner.seen[0]["evidence"])
    first = next(event[2] for event in events if event[0] == "round-1-candidate")
    assert first["nodes"][0]["plugin_id"] == "x.default"
    assert result["candidate"]["nodes"][0]["plugin_id"] == "x.final"
    assert result["rounds"] == 2
    assert result["assessment"]["passed"] is True
    assert any(event[1] == "round_finished" for event in events)
    assert result["mode"] == "plan_only"


def test_missing_second_round_evidence_cannot_pass() -> None:
    ctx, request, _ = scenario()
    sources = Sources(ctx)
    original = sources.query

    def query(context, q):
        if "补充" in q.query:
            sources.calls.append(q)
            return SourceResult(source_kind=q.source_kind, status="empty")
        return original(context, q)

    sources.query = query

    class EvidenceReviewer(Reviewer):
        def review(self, context, complete_json):
            if not any("补充" in row["content"] for row in context["evidence"]):
                return super().review({**context, "round_no": 1}, complete_json)
            return super().review(context, complete_json)

    engine, _, _ = make_engine(ctx, sources=sources, reviewer=EvidenceReviewer())
    assert engine.run(ctx, request)["status"] != "accepted"


def test_policy_denial_does_not_read_any_source() -> None:
    ctx, request, _ = scenario()

    def deny(capability, arguments):
        raise PermissionError("source denied")

    sources = Sources(ctx)
    engine, _, _ = make_engine(ctx, sources=sources, gateway=deny)
    result = engine.run(ctx, request)
    assert result["status"] == "blocked_policy"
    assert sources.calls == []


def test_cross_tenant_evidence_is_rejected() -> None:
    ctx, request, _ = scenario()
    sources = Sources(ctx)
    original = sources.query

    def bad(context, query):
        result = original(context, query)
        return result.model_copy(update={"items": [result.items[0].model_copy(update={"tenant_id": uuid4()})]})

    sources.query = bad
    engine, _, _ = make_engine(ctx, sources=sources)
    assert engine.run(ctx, request)["status"] == "failed"


def test_total_query_budget_cannot_be_extended_by_the_reviewer() -> None:
    ctx, request, _ = scenario()
    request = request.model_copy(update={"budget": request.budget.model_copy(update={"max_queries": 3})})
    engine, _, _ = make_engine(ctx)
    result = engine.run(ctx, request)
    assert result["status"] == "budget_exhausted"
    assert len(engine.sources.calls) == 3


def test_sources_unavailable_are_not_treated_as_empty() -> None:
    ctx, request, _ = scenario()
    sources = Sources(ctx)
    sources.query = lambda context, query: SourceResult(source_kind=query.source_kind, status="error", detail="unavailable")
    engine, _, _ = make_engine(ctx, sources=sources)
    assert engine.run(ctx, request)["status"] == "failed"
