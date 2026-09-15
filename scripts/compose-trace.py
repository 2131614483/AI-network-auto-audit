"""Print the composition trace: which agent produced which part of a plan.

用法::

    # 审计域，按目标召回（中文不加分隔符也能召回）
    .\\.venv\\Scripts\\python.exe scripts\\compose-trace.py --goal "资金舞弊专项审计"

    # 指定领域；或显式选定全部插件（等价于旧驱动脚本的 select=VERIFIED）
    .\\.venv\\Scripts\\python.exe scripts\\compose-trace.py --domain aiops --all
    .\\.venv\\Scripts\\python.exe scripts\\compose-trace.py --domain audit --all --json

为什么需要它：一次只报"100/100 succeeded"的运行，说不出**是哪一步**决定了那条主链、
那 15 个孤岛是怎么来的。这个脚本把组网拆成 agent 的逐步决策并把每步的产出打出来 ——
计划因此可解释，而不是一个"神谕式"的结论。

只读：不执行任何插件、不写任何产物，也**不编译**（编译仍是 compile_plan 的唯一职责；
本脚本只在你显式加 --compile 时才调它做一次校验）。

退出码: 0 = 正常；1 = 组网或编译失败（fail-closed，绝不把失败伪装成成功）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.ai_planner.composer import compile_flow, discover_plugins  # noqa: E402
from packages.ai_planner.composition import compose_with_trace  # noqa: E402
from packages.ai_planner.domain_pack import available_domains, load_pack  # noqa: E402

_DETAIL_KEYS = (
    "data_edges", "data_edges_by_kind", "call_edges", "dropped_data_edges",
    "islands", "by_category", "count", "by_role", "confirmed_groups",
    "suggested_groups", "recalled_count", "planned_count", "cycle_dropped",
    "untrusted_schema_only_edges", "external_inputs",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--goal", default="全流程", help="组网目标（用于关键词召回）")
    parser.add_argument("--domain", choices=available_domains(), default="audit")
    parser.add_argument("--all", action="store_true", help="选定该领域全部插件（不做目标召回）")
    parser.add_argument("--plan-key", default=None)
    parser.add_argument("--compile", action="store_true", help="额外做一次编译校验")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出整条 trace")
    args = parser.parse_args(argv)

    pack = load_pack(args.domain)
    select = sorted(discover_plugins(globs=pack.globs)) if args.all else None
    try:
        composition = compose_with_trace(
            goal=args.goal, select=select, domain=args.domain, plan_key=args.plan_key,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed
        print(f"组网失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(composition.trace.as_dict(), ensure_ascii=False, indent=2))
    else:
        trace = composition.trace
        scope = "全部插件" if select else f"按目标召回：{args.goal}"
        print(f"# 组网 trace — 领域={trace.domain}  {scope}")
        print()
        for step in trace.steps:
            record = step.as_dict()
            flag = " [模型]" if step.used_model else ""
            print(f"  {record['agent_zh']}({record['agent']}){flag} → {record['produced']}")
            for key in _DETAIL_KEYS:
                if key in step.detail:
                    value = step.detail[key]
                    if isinstance(value, dict):
                        value = json.dumps(value, ensure_ascii=False)
                    print(f"      {key}: {value}")
            for issue in step.issues:
                print(f"      ⚠ {issue.code}: {issue.message}")
        print()
        flow = composition.flow
        print(f"结果：节点={len(flow['nodes'])} 边={len(flow['edges'])} 种子={len(flow['seed_inputs'])}")

    if args.compile:
        plan = compile_flow(composition.flow)
        if not args.json:
            print(f"编译校验通过：hash={plan.execution_hash[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
