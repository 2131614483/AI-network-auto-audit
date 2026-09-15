"""第二种 AI 接入 — 远端 OpenAI 兼容模型真实冒烟（opt-in，用户显式运行）。

用法（请先设置环境变量，key 不落盘）::

    $env:OPENAI_COMPAT_API_KEY="<你的 key>"
    .\\.venv\\Scripts\\python.exe scripts\\ai-planning-remote-smoke.py

默认端点 https://api.commandcode.ai/provider/v1，模型 meituan/LongCat-2.0:free
（可用 OPENAI_COMPAT_BASE_URL / OPENAI_COMPAT_MODEL 覆盖）。

对 20 意图评测集的首个意图（s1 ledger-backtest）调用真实远端模型，
草稿仍走 AiPlanner 全部闸门（能力召回/数据边界/编译），只输出
draft_ready 或 gap_report 及失败原因；真实网络调用由本脚本显式触发，
不随任何自动测试运行。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.ai_planner.evaluation import EVALUATION_CASES, catalog_for  # noqa: E402
from packages.ai_planner.planner import AiPlanner  # noqa: E402
from packages.llm.openai_compat_client import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OpenAICompatChat,
    OpenAICompatChatError,
    is_available,
)


def main() -> int:
    if not is_available():
        print("OPENAI_COMPAT_API_KEY 未设置；拒绝无凭据调用远端模型。", file=sys.stderr)
        print("请先执行: $env:OPENAI_COMPAT_API_KEY=\"<your key>\"", file=sys.stderr)
        return 2

    case = EVALUATION_CASES[0]
    print(f"远端后端: {DEFAULT_BASE_URL}")
    print(f"模型: {DEFAULT_MODEL}（OPENAI_COMPAT_MODEL 可覆盖）")
    print(f"意图: {case.case_id} / {case.goal}")
    print("真实网络调用中（timeout 默认 180s）……")

    try:
        chat = OpenAICompatChat()
    except OpenAICompatChatError as exc:
        print(f"初始化失败: {exc}", file=sys.stderr)
        return 2

    planner = AiPlanner(chat)
    outcome = planner.plan(
        goal=case.goal,
        catalog=catalog_for(case),
        authorized_sources=set(case.authorized_sources),
    )
    print(f"\nstatus: {outcome.status}  revisions: {outcome.revisions}")
    if outcome.status == "draft_ready":
        assert outcome.execution_plan is not None
        print("plan_key:", outcome.execution_plan.plan_key)
        print("nodes:", [n["capability"] for n in outcome.execution_plan.nodes])
        print("edges:", [e["edge_id"] for e in outcome.execution_plan.edges])
        print("种子（合法边界内）: 已校验")
        print("\n远端模型草稿通过全部闸门 → draft_ready（未创建任何运行）。")
        return 0
    print("缺口报告:")
    for issue in outcome.issues:
        print(f"  - [{issue['code']}] {issue['message']}")
    print("\n远端草稿被安全拒绝 → gap_report（未产生可执行计划）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
