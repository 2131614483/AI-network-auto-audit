"""CW5 canvas chat contract tests: goal compression, reply text and request
shape — all offline (no database, no model).

Acceptance:
  - a chat goal carries prior turns + an existing base_draft so the model
    revises the same flow;
  - replies read naturally for draft_ready and gap_report (never a fake run);
  - the request schema is closed (extra fields rejected, bounds enforced).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from apps.api.main import CanvasChatRequest, ChatTurn
from packages.ai_planner import TEMPLATES, PlanningOutcome
from packages.ai_planner.chat import CanvasChatPlanner, build_chat_goal, reply_text_for


class _FakePlanner:
    """Injected planner capturing the goal passed by CanvasChatPlanner."""

    def __init__(self, outcome: PlanningOutcome) -> None:
        self.outcome = outcome
        self.goals: list[str] = []

    def plan(self, **kwargs: Any) -> PlanningOutcome:
        self.goals.append(str(kwargs["goal"]))
        return self.outcome


def _template_draft(**overrides: Any) -> dict[str, Any]:
    template = TEMPLATES["ledger-backtest"]
    draft = {
        "plan_key": "plan-chat-1",
        "nodes": template["nodes"],
        "edges": template["edges"],
        "budget": template["budget"],
        "seed_inputs": [["ledger-a", "ledger"]],
        "selection_reasons": "revised from the base draft",
    }
    draft.update(overrides)
    return draft


def _outcome(status: str, *, draft: dict[str, Any] | None = None,
             issues: list[dict[str, Any]] | None = None, revisions: int = 1) -> PlanningOutcome:
    return PlanningOutcome(
        status=status,
        plan_key="plan-chat-1",
        execution_plan=None,
        draft=draft,
        revisions=revisions,
        issues=issues or [],
    )


# -- goal compression -----------------------------------------------------------


def test_build_chat_goal_includes_history_and_current_message() -> None:
    goal = build_chat_goal(
        "把输出改为只读回测",
        history=[
            {"role": "user", "content": "校验日记账质量"},
            {"role": "assistant", "content": "已生成 2 节点 1 边草稿。"},
        ],
    )
    assert "校验日记账质量" in goal
    assert "已生成 2 节点 1 边草稿" in goal
    assert "把输出改为只读回测" in goal
    # the current message is always present even with empty history
    single = build_chat_goal("新建一条审计链", history=[])
    assert "新建一条审计链" in single


def test_build_chat_goal_caps_history_to_recent_turns() -> None:
    history = [
        {"role": "user", "content": f"消息{i}"}
        for i in range(12)
    ]
    goal = build_chat_goal("当前", history=history)
    lines = set(goal.splitlines())
    for old in (0, 1, 2):
        assert f"用户：消息{old}" not in lines  # oldest turns are dropped
    assert "用户：消息11" in lines
    assert "当前" in goal


def test_build_chat_goal_mentions_base_draft_nodes_and_edges() -> None:
    draft = _template_draft()
    goal = build_chat_goal("在这基础上加一个证据校验节点", base_draft=draft)
    assert "既有画布草稿" in goal
    assert "2 个节点" in goal
    assert "1 条边" in goal
    for node in draft["nodes"]:
        assert node["node_instance_id"] in goal


def test_build_chat_goal_tolerates_empty_base_draft() -> None:
    goal = build_chat_goal("从零规划", base_draft={"nodes": [], "edges": []})
    assert "0 个节点" in goal
    assert "既有画布草稿" in goal


# -- reply text ----------------------------------------------------------------


def test_reply_text_draft_ready_reports_graph() -> None:
    draft = _template_draft()
    text = reply_text_for(_outcome("draft_ready", draft=draft))
    assert "2 个节点" in text
    assert "1 条边" in text
    assert "plan-chat-1" in text
    assert "选型说明" in text


def test_reply_text_gap_report_is_honest() -> None:
    issues = [{"code": "capability_unavailable", "message": "no such plugin"}]
    text = reply_text_for(_outcome("gap_report", issues=issues))
    assert "capability_unavailable" in text
    assert "fail-closed" in text or "不会伪造运行" in text


# -- request contract ----------------------------------------------------------


def test_canvas_chat_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CanvasChatRequest(
            message="校验日记账", idempotency_key="k" * 12, dangerous_extra=True,
        )


def test_canvas_chat_request_validates_bounds() -> None:
    with pytest.raises(ValidationError):
        CanvasChatRequest(message="", idempotency_key="k" * 12)
    with pytest.raises(ValidationError):
        CanvasChatRequest(message="ok", idempotency_key="short")
    with pytest.raises(ValidationError):
        CanvasChatRequest(
            message="ok", idempotency_key="k" * 12,
            history=[ChatTurn(role="user", content="")],
        )


def test_canvas_chat_request_accepts_valid_payload() -> None:
    request = CanvasChatRequest(
        message="校验日记账质量并回测",
        session_id="chat-" + "a" * 12,
        history=[ChatTurn(role="user", content="校验日记账质量")],
        base_draft=_template_draft(),
        data_sources=[["ledger-a", "ledger"]],
        budget={"max_chain_length": 4, "max_candidates": 6, "max_latency_ms": 300000},
        template_keys=["ledger-backtest"],
        idempotency_key="k" * 12,
    )
    assert request.message == "校验日记账质量并回测"
    assert request.history[0].role == "user"
    assert request.base_draft is not None


# -- CanvasChatPlanner wiring ---------------------------------------------------


def test_canvas_chat_planner_passes_compressed_goal_to_ai_planner() -> None:
    fake = _FakePlanner(_outcome("draft_ready", draft=_template_draft()))
    planner = CanvasChatPlanner(fake)  # type: ignore[arg-type]
    outcome = planner.chat(
        message="加一个证据校验节点",
        catalog={},
        authorized_sources={("ledger-a", "ledger")},
        history=[{"role": "assistant", "content": "已生成图谱流程。"}],
        base_draft=_template_draft(),
    )
    assert outcome.status == "draft_ready"
    assert fake.goals, "planner must receive a goal"
    assert "加一个证据校验节点" in fake.goals[0]
    assert "既有画布草稿" in fake.goals[0]


# -- AiPlanner progress events (streaming stages) ------------------------------


def _test_catalog() -> dict[str, Any]:
    """A recall whitelist derived from the ledger-backtest template so the
    model drafts validate against *some* real capabilities."""
    from packages.ai_planner.catalog import CapabilityEntry

    template = TEMPLATES["ledger-backtest"]
    entries: dict[str, Any] = {}
    for node in template["nodes"]:
        entries[node["capability"]] = CapabilityEntry(
            capability=node["capability"],
            plugin_id=node["plugin_id"],
            inputs=tuple(port["port_id"] for port in node.get("input_ports") or []),
            outputs=tuple(port["port_id"] for port in node.get("output_ports") or []),
        )
    return entries


class _StageProbe:
    """Injected model whose drafts fail validation once, then compile."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, messages: list[dict[str, str]], *, temperature: float) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            # a draft with an unknown capability fails validation
            return _template_draft(nodes=[{**_template_draft()["nodes"][0], "capability": "made.up.capability"}])
        return _template_draft()


def test_ai_planner_emits_progress_stage_sequence() -> None:
    from packages.ai_planner.planner import AiPlanner

    stages: list[dict[str, Any]] = []
    probe = _StageProbe()
    outcome = AiPlanner(probe, max_revision_rounds=2).plan(
        goal="校验日记账",
        catalog=_test_catalog(),
        authorized_sources={("ledger-a", "ledger")},
        on_progress=stages.append,
    )
    # first draft fails validation -> second round revises -> compile ok -> done
    names = [stage["stage"] for stage in stages]
    assert names[0] == "capability_recall"
    assert names[1] == "llm_draft"
    assert "validate" in names
    assert "compile" in names
    assert names[-1] == "done"
    assert outcome.status == "draft_ready"
    assert probe.calls == 2
    validate_events = [s for s in stages if s["stage"] == "validate"]
    assert validate_events[0]["ok"] is False
    assert validate_events[1]["ok"] is True


def test_ai_planner_progress_without_callback_is_noop() -> None:
    from packages.ai_planner.planner import AiPlanner

    probe = _StageProbe()
    outcome = AiPlanner(probe, max_revision_rounds=2).plan(
        goal="校验日记账",
        catalog=_test_catalog(),
        authorized_sources={("ledger-a", "ledger")},
    )
    assert outcome.status == "draft_ready"  # no callback -> no events, same result


# -- registered port contract directory (方案 5.1 / 6.1) -----------------------


def test_validate_draft_rejects_unregistered_port() -> None:
    from packages.ai_planner.draft import validate_draft

    draft = _template_draft()
    node = draft["nodes"][0]
    node = {**node, "output_ports": [
        {**node["output_ports"][0], "port_id": "made-up-output"},
    ]}
    validation = validate_draft({**draft, "nodes": [node]}, _test_catalog(), {("ledger-a", "ledger")})
    assert validation.ok is False
    assert validation.issue is not None
    assert validation.issue.code == "contract_mismatch"
    assert "not in the registered contract directory" in validation.issue.message


def test_validate_draft_rejects_deviated_contract_field() -> None:
    from packages.ai_planner.draft import validate_draft

    draft = _template_draft()
    node = draft["nodes"][0]
    node = {**node, "output_ports": [
        {**node["output_ports"][0], "schema_ref": "invented-schema"},
    ]}
    validation = validate_draft({**draft, "nodes": [node]}, _test_catalog(), {("ledger-a", "ledger")})
    assert validation.ok is False
    assert validation.issue is not None
    assert validation.issue.code == "contract_mismatch"
    assert "deviates from registered contract field schema_ref" in validation.issue.message


def test_validate_draft_accepts_registered_ports_verbatim() -> None:
    from packages.ai_planner.draft import validate_draft

    validation = validate_draft(_template_draft(), _test_catalog(), {("ledger-a", "ledger")})
    assert validation.ok is True  # template ports reproduce the registry verbatim


def test_recall_snapshot_carries_port_contracts() -> None:
    from packages.ai_planner.catalog import recall_snapshot

    text = recall_snapshot(_test_catalog())
    assert "schema_ref" in text
    assert "ledger-artifact-ref" in text
    assert "audit-quality-candidates" in text
    assert "原样复制" in text


def test_validate_draft_accepts_external_canvas_seed_source() -> None:
    """A seed source authorized from the canvas (an existing data outlet that is
    NOT declared among this draft's nodes) must be legal — the new flow consumes
    a data file the canvas already produces."""
    from packages.ai_planner.draft import validate_draft

    draft = _template_draft()
    # drop ledger-a from the draft nodes entirely: the ledger data now comes
    # from an external canvas node "external-ledger-a"
    draft = {**draft, "nodes": draft["nodes"][1:]}
    draft = {
        **draft,
        "seed_inputs": [["external-ledger-a", "ledger"]],
        "edges": [],
    }
    validation = validate_draft(
        draft,
        _test_catalog(),
        {("external-ledger-a", "ledger")},
    )
    assert validation.ok is True
    assert validation.issue is None


def test_planner_auto_binds_canvas_outlet_to_dangling_required_input() -> None:
    """When the model omits seed_inputs, a canvas data outlet exposing the same
    registered port as a node's dangling required input is bound deterministically
    — data binding comes from the canvas context, not from model memory.  The
    compiler-side seed is expressed on THIS flow node's port; the canvas outlet
    stays as provenance."""
    from packages.ai_planner.planner import _auto_seed_inputs

    draft = _template_draft()
    seeds = _auto_seed_inputs(
        draft["nodes"],
        draft["edges"],
        {("external-ledger-a", "ledger")},
    )
    # the dangling required input is ledger-a.ledger in this draft's nodes
    assert ("ledger-a", "ledger") in seeds
    # a consumed input (has an incoming edge) must NOT be auto-seeded
    seeds2 = _auto_seed_inputs(
        draft["nodes"],
        draft["edges"],
        {("external-x", "experiment")},
    )
    assert ("consumer-x", "experiment") not in seeds2


def test_planner_compiles_draft_without_model_seed_via_auto_binding() -> None:
    from packages.ai_planner.planner import AiPlanner

    class _NoSeedProbe:
        calls = 0

        def complete_json(self, messages: list[dict[str, str]], *, temperature: float) -> dict[str, Any]:
            self.calls += 1
            draft = _template_draft()
            return {**draft, "seed_inputs": []}  # model never writes seed_inputs

    probe = _NoSeedProbe()
    outcome = AiPlanner(probe, max_revision_rounds=1).plan(
        goal="校验日记账",
        catalog=_test_catalog(),
        authorized_sources={("external-ledger-a", "ledger")},
    )
    assert outcome.status == "draft_ready"  # auto seed made the required input satisfiable
    assert probe.calls == 1
