from __future__ import annotations

import importlib.util
from pathlib import Path

from packages.ai_planner.collaborative import CollaborativePlanner
from packages.ai_planner.evidence import SourceQuery, fingerprint

_spec = importlib.util.spec_from_file_location(
    "audit_loop_helpers", Path(__file__).with_name("test_collaborative_loop.py")
)
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)


def engine_for(brain):
    context, request, specs = helpers.scenario()
    sources = helpers.Sources(context)
    planner = helpers.Planner()
    events = []
    engine = CollaborativePlanner(
        sources=sources,
        gateway=lambda _cap, _args: "decision",
        model=None,
        planner=planner,
        reviewer=helpers.Reviewer(),
        brain=brain,
        specs=specs,
        emit=lambda key, kind, payload, state: events.append((key, kind, payload, state)),
    )
    return context, request, engine, sources, planner, events


def test_brain_sees_knowledge_before_graph_history_or_plugin_directory():
    brain = helpers.Brain()
    context, request, engine, sources, planner, _ = engine_for(brain)
    result = engine.run(context, request)
    assert result["status"] == "accepted", result
    assert sources.calls[0].source_kind == "knowledge"
    assert brain.seen and all(
        item["source_kind"] == "knowledge" for item in brain.seen[0]["evidence"]
    )
    assert "catalog" not in brain.seen[0] and "adapters" not in brain.seen[0]
    assert planner.seen[0]["audit_brief"]
    assert result["assessment"]["audit_brief_hash"] == fingerprint(result["audit_brief"])


def test_blocked_audit_basis_triggers_knowledge_lookup_before_plugin_access():
    class BlockedBrain(helpers.Brain):
        def build_brief(self, context, complete_json):
            brief = super().build_brief(context, complete_json)
            if not any("补充" in item["content"] for item in context["evidence"]):
                return brief.model_copy(
                    update={
                        "procedures": [],
                        "blockers": ["缺适用条件正文"],
                        "followup_queries": [
                            SourceQuery(
                                source_kind="knowledge", query="补充适用条件", purpose="补审计依据"
                            )
                        ],
                    }
                )
            return brief

    brain = BlockedBrain()
    context, request, engine, sources, planner, _ = engine_for(brain)
    result = engine.run(context, request)
    assert result["status"] == "accepted", result
    assert [q.source_kind for q in sources.calls[:4]] == [
        "knowledge",
        "knowledge",
        "graph",
        "history",
    ]
    assert len(planner.seen) == 1 and planner.seen[0]["round_no"] == 2


def test_unresolvable_audit_basis_never_reaches_plugin_planner():
    class BlockedBrain(helpers.Brain):
        def build_brief(self, context, complete_json):
            return (
                super()
                .build_brief(context, complete_json)
                .model_copy(update={"procedures": [], "blockers": ["没有可用的审计方法依据"]})
            )

    context, request, engine, sources, planner, _ = engine_for(BlockedBrain())
    result = engine.run(context, request)
    assert result["status"] == "stalled"
    assert planner.seen == [] and [q.source_kind for q in sources.calls] == ["knowledge"]


def test_new_knowledge_regenerates_business_brief_before_replanning():
    brain = helpers.Brain()
    context, request, engine, _, planner, _ = engine_for(brain)
    result = engine.run(context, request)
    assert result["status"] == "accepted", result
    assert len(brain.seen) == 2 and len(planner.seen) == 2
    assert planner.seen[0]["audit_brief_hash"] != planner.seen[1]["audit_brief_hash"]


def test_non_audit_domain_keeps_its_existing_planning_flow():
    class ForbiddenBrain:
        def build_brief(self, context, complete_json):
            raise AssertionError("non-audit planning must not invent an audit program")

    context, request, engine, _, planner, _ = engine_for(ForbiddenBrain())
    request = request.model_copy(update={"domain": "knowledge"})
    result = engine.run(context, request)
    assert result["status"] == "accepted", result
    assert result["audit_brief"] is None
    assert planner.seen[0]["audit_brief"] is None


def test_model_context_keeps_one_source_representation_and_full_evidence_for_verification():
    brain = helpers.Brain()
    context, request, engine, sources, planner, _ = engine_for(brain)
    original_query = sources.query

    def query(ctx, source_query):
        result = original_query(ctx, source_query)
        return result.model_copy(
            update={
                "items": [
                    item.model_copy(update={"facts": {"content_copy": item.content}})
                    for item in result.items
                ]
            }
        )

    sources.query = query
    result = engine.run(context, request)
    assert result["status"] == "accepted", result
    assert result["evidence"][0]["facts"]
    assert all(
        "facts" not in item and "sha256" in item and "origin_refs" in item
        for item in planner.seen[0]["evidence"]
    )
    assert all("facts" not in item for item in brain.seen[0]["evidence"])


def test_reviewer_audit_revision_precedes_deferred_plugin_queries():
    steps = []

    class Brain(helpers.Brain):
        def build_brief(self, context, complete_json):
            steps.append("brain")
            brief = super().build_brief(context, complete_json)
            return brief.model_copy(update={"scope": f"business scope revision {len(self.seen)}"})

    class Reviewer(helpers.Reviewer):
        def review(self, context, complete_json):
            review = super().review(context, complete_json)
            if context["round_no"] == 1:
                return review.model_copy(
                    update={
                        "return_to_audit_brain": True,
                        "followup_queries": [
                            SourceQuery(
                                source_kind="history",
                                query="other plugin experience",
                                purpose="后续优化组合",
                            )
                        ],
                    }
                )
            return review.model_copy(
                update={
                    "blockers": [],
                    "followup_queries": [],
                    "requirements": [
                        x.model_copy(update={"met": True}) for x in review.requirements
                    ],
                    "procedures": [x.model_copy(update={"met": True}) for x in review.procedures],
                }
            )

    context, request, engine, sources, _, _ = engine_for(Brain())
    engine.reviewer = Reviewer()
    original = sources.query

    def query(ctx, q):
        steps.append("query:" + q.source_kind)
        return original(ctx, q)

    sources.query = query
    result = engine.run(context, request)
    assert result["status"] == "accepted", result
    assert steps == [
        "query:knowledge",
        "brain",
        "query:graph",
        "query:history",
        "brain",
        "query:history",
    ]
