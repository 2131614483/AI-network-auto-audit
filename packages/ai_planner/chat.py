"""CW5 画布对话：把画布 AI 聊天框的"布置任务 / 交流 / 修订"折叠成
AiPlanner 的受控规划请求。

聊天本身不引入新的执行权限：模型只输出结构化草稿，草稿必须通过能力召回、
数据边界与确定性编译闸门（``AiPlanner.plan``）才成为画布图谱流程；编译失败
或模型不可用时 fail-closed 返回 gap report / 错误，绝不伪造运行。
"""

from __future__ import annotations

from typing import Any

from .planner import AiPlanner, PlanningOutcome, ProgressCallback

_MAX_HISTORY_TURNS = 6


def build_chat_goal(
    message: str,
    *,
    history: list[dict[str, str]] | None = None,
    base_draft: dict[str, Any] | None = None,
) -> str:
    """Compress prior turns + the current message + an existing canvas draft
    into one planner goal so the model revises *the same* flow rather than
    starting from scratch.

    ``history`` entries are ``{"role": "user"|"assistant", "content": str}``.
    Only the most recent ``_MAX_HISTORY_TURNS`` turns are kept so a long chat
    cannot blow the planner prompt budget.
    """
    lines: list[str] = []
    for turn in (history or [])[-_MAX_HISTORY_TURNS:]:
        role = "助手" if turn.get("role") == "assistant" else "用户"
        content = str(turn.get("content") or "").strip()
        if content:
            lines.append(f"{role}：{content}")
    if base_draft:
        nodes = list(base_draft.get("nodes") or [])
        edges = list(base_draft.get("edges") or [])
        node_ids = ", ".join(
            str(n.get("node_instance_id") or "?") for n in nodes[:24]
        ) or "(空)"
        lines.append(
            f"既有画布草稿（请在其基础上修订，保留仍需要的节点）："
            f"{len(nodes)} 个节点、{len(edges)} 条边；节点：{node_ids}"
        )
    lines.append(f"用户（当前）：{message}")
    return "\n".join(lines)


def reply_text_for(outcome: PlanningOutcome) -> str:
    """User-facing assistant reply for the chat panel."""
    if outcome.status == "draft_ready" and outcome.draft:
        nodes = list(outcome.draft.get("nodes") or [])
        edges = list(outcome.draft.get("edges") or [])
        reasons = str(outcome.draft.get("selection_reasons") or "").strip()
        base = (
            f"已生成图谱流程：{len(nodes)} 个节点、{len(edges)} 条边"
            f"（{outcome.plan_key}，第 {outcome.revisions} 版即通过确定性编译）。"
        )
        return f"{base}{'选型说明：' + reasons if reasons else ''}"
    codes = sorted({str(issue.get("code") or "unknown") for issue in outcome.issues})
    return (
        "未能生成可编译的图谱流程："
        + ("、".join(codes) if codes else "未知缺口")
        + "。已按 fail-closed 停止，不会伪造运行；请补充能力契约或调整目标后重试。"
    )


class CanvasChatPlanner:
    """One-turn planner wrapper for the canvas chat endpoint.

    ``planner`` is injectable (real ``AiPlanner`` in production, fakes in
    tests).  Multi-turn state lives in the client; every request carries its
    history and optional ``base_draft``, keeping the backend stateless.
    """

    def __init__(self, planner: AiPlanner) -> None:
        self.planner = planner

    def chat(
        self,
        *,
        message: str,
        catalog: dict[str, Any],
        authorized_sources: set[tuple[str, str]],
        history: list[dict[str, str]] | None = None,
        base_draft: dict[str, Any] | None = None,
        budget: dict[str, int] | None = None,
        template_keys: tuple[str, ...] | None = None,
        on_progress: ProgressCallback = None,
    ) -> PlanningOutcome:
        goal = build_chat_goal(message, history=history, base_draft=base_draft)
        return self.planner.plan(
            goal=goal,
            catalog=catalog,
            authorized_sources=authorized_sources,
            budget=budget,
            template_keys=template_keys or (),
            on_progress=on_progress,
        )
