"""AI 组网 20 意图评测 — 真实本地模型（opt-in，不随全量测试运行）。

用法（需本地 Ollama 且模型已拉取）::

    .\\.venv\\Scripts\\python.exe scripts\\evaluate-ai-planning.py

对方案验收矩阵 :463 的 20 个固定业务意图逐个调用真实 AiPlanner
（本地 qwen3 27B 64K），记录每次修订、失败原因、耗时与目标覆盖，
输出 JSON 报告到 ``.data/evaluation/ai_planning_real_report-<ts>.json``。

退出码：0 = 20 个意图全部通过闸门断言；1 = 存在失败；2 = 模型不可用。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.ai_planner.evaluation import EVALUATION_CASES, evaluate_intents  # noqa: E402
from packages.ai_planner.planner import AiPlanner  # noqa: E402
from packages.llm.ollama_client import OllamaChat, OllamaChatError, _is_available  # noqa: E402


def main() -> int:
    if not _is_available():
        print("本地 Ollama 不可用（http://127.0.0.1:11434 /api/tags 无响应）。", file=sys.stderr)
        print("请先启动 ollama serve 并拉取模型：ollama pull qwen3_27b_iq3xxs_64k", file=sys.stderr)
        return 2

    try:
        chat = OllamaChat()
    except OllamaChatError as exc:  # pragma: no cover - construction is cheap
        print(f"模型初始化失败: {exc}", file=sys.stderr)
        return 2

    print(f"模型: {chat.model}  num_ctx={chat.num_ctx}")
    print(f"意图数: {len(EVALUATION_CASES)}（成功 7 / 含糊 3 / 缺插件 4 / 错数据域 3 / 恶意 3）")
    print("逐个真实推理中（每意图最多 3 轮修订），可耐心等待……\n")

    planner = AiPlanner(chat)
    report = evaluate_intents(planner=planner)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / ".data" / "evaluation" / f"ai_planning_real_report-{ts}.json"
    report.write_json(out)

    summary = report.as_dict()["summary"]
    print(f"=== 真实模型评测摘要（{ts}）===")
    print(f"总计 {summary['total']} | 通过 {summary['passed']} | "
          f"draft_ready {summary['draft_ready']} | gap_report {summary['gap_report']} | "
          f"拒绝安全 {summary['rejected_safely']} | 覆盖达标 {summary['coverage_ok_count']}")
    for case in report.cases:
        mark = "PASS" if case.passed else "FAIL"
        detail = f"revisions={case.revisions} codes={case.codes}" if case.status == "gap_report" \
            else f"caps={case.plan_capabilities} coverage={case.coverage}"
        print(f"  [{mark}] {case.case_id:<26} {case.category:<14} {case.status:<11} "
              f"{case.elapsed_ms:>6}ms {detail}")
    print(f"\n报告: {out}")

    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
