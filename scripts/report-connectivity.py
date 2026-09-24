"""Connectivity / provenance report for the audit plugin network (read-only).

用法::

    # 当前契约目录的连通性（默认全量 107 个插件的 contract 口径）
    .\\.venv\\Scripts\\python.exe scripts\\report-connectivity.py

    # 只算 verified（可运行）插件；把 invokes 跨层调用也计入（口径会标注）
    .\\.venv\\Scripts\\python.exe scripts\\report-connectivity.py --lifecycle verified
    .\\.venv\\Scripts\\python.exe scripts\\report-connectivity.py --include-invokes

    # 冻结当前口径为基线（改造前的"前"列）
    .\\.venv\\Scripts\\python.exe scripts\\report-connectivity.py --write-baseline path.json

    # 与基线对比；有回归则非零退出（CI 闸门）
    .\\.venv\\Scripts\\python.exe scripts\\report-connectivity.py --baseline path.json

    # 对一次已完成的运行做"输入 sha256 == 上游输出 sha256"可追溯性审计
    .\\.venv\\Scripts\\python.exe scripts\\report-connectivity.py --attempts runs.json

退出码: 0 = 通过；1 = 连通性回归或有溯源违例；2 = 参数/口径错误（拒绝比较不同口径的数）。

绝不做的事：不改任何计划、不执行插件、不写任何产物（`--write-baseline` 是显式指定的唯一写入口）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.ai_planner.connectivity import (  # noqa: E402
    ConnectivityMetrics,
    format_metrics_table,
    format_provenance_table,
    provenance_report,
)
from packages.catalog.connectivity_view import connectivity_report  # noqa: E402

# 指标方向：+1 越大越好，-1 越小越好。用于判断"回归"。
_DIRECTION = {
    "plugins": 0,
    "edges": +1,
    "isolated_nodes": -1,
    "no_incoming": -1,
    "no_outgoing": -1,
    "weakly_connected_components": -1,
    "dead_end_outputs": -1,
    "unfillable_inputs": -1,
    "unfillable_input_ports": -1,
    "total_input_ports": 0,
    "seeds": -1,
    "pure_seed_nodes": -1,
    "reusable_contracts": +1,
}


def _edge_set_mismatch(baseline: dict, current: dict) -> str | None:
    before, after = baseline.get("edge_set"), current.get("edge_set")
    if before != after:
        return f"口径不同（{before} → {after}），拒绝比较 —— 连通性指标只在同一图定义下可比"
    return None


def _regressions(baseline: dict, current: dict) -> list[str]:
    problems: list[str] = []
    for key, direction in _DIRECTION.items():
        if direction == 0 or key not in baseline:
            continue
        before, after = baseline[key], current[key]
        if direction > 0 and after < before:
            problems.append(f"{key} 退化：{before} → {after}")
        elif direction < 0 and after > before:
            problems.append(f"{key} 退化：{before} → {after}")
    return problems


def _as_metrics(payload: dict) -> ConnectivityMetrics:
    """Rebuild a ConnectivityMetrics from a frozen baseline JSON."""
    fields = ConnectivityMetrics.__dataclass_fields__
    kwargs = {k: payload[k] for k in fields if k in payload and k != "component_sizes"}
    kwargs["component_sizes"] = tuple(payload.get("component_sizes") or ())
    return ConnectivityMetrics(**kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lifecycle", choices=["verified", "contract_only"], default=None,
                        help="只统计该 lifecycle 的插件；缺省为全部")
    parser.add_argument("--include-invokes", action="store_true",
                        help="把 invokes 跨层调用计入（口径标注为 contract+invokes）")
    parser.add_argument("--baseline", type=Path, default=None, help="与冻结基线对比，回归则非零退出")
    parser.add_argument("--write-baseline", type=Path, default=None, help="把当前口径写为基线 JSON")
    parser.add_argument("--attempts", type=Path, default=None, help="node_attempts JSON，做溯源审计")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（便于归档/流水线）")
    args = parser.parse_args(argv)

    if args.attempts is not None:
        payload = json.loads(args.attempts.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else payload.get("attempts") or []
        report = provenance_report(records)
        if args.json:
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"# 数据可追溯审计 — {args.attempts}")
            print()
            print(format_provenance_table(report))
            print()
            if report.violations:
                print(f"违例明细（{len(report.violations)} 条）:")
                for violation in report.violations[:20]:
                    print(f"  - {violation.node_instance_id}.{violation.port_id} "
                          f"[{violation.reason}] {violation.detail}")
                if len(report.violations) > 20:
                    print(f"  … 另有 {len(report.violations) - 20} 条")
            else:
                print("全部直连边的输入 sha256 与上游产出逐字节一致。")
        return 0 if report.ok else 1

    # 口径来自 packages/catalog/connectivity_view.py —— 与只读端点共用同一份实现，
    # 免得脚本与界面各算一套、对同一张网给出相反的结论。
    report = connectivity_report(lifecycle=args.lifecycle, include_invokes=args.include_invokes)
    current = report["metrics"]
    metrics = _as_metrics(current)

    if args.write_baseline is not None:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        print(f"基线已写入 {args.write_baseline}", file=sys.stderr)

    if args.json:
        print(json.dumps(current, ensure_ascii=False, indent=2))
    else:
        scope = args.lifecycle or "全部"
        print(f"# 连通性报告 — lifecycle={scope}  图口径={metrics.edge_set}")
        print()
        for key, label in (
            ("plugins", "插件数"), ("edges", "边数"),
            ("isolated_nodes", "完全孤立节点"), ("no_incoming", "无入边节点"),
            ("no_outgoing", "无出边节点"), ("weakly_connected_components", "弱连通分量"),
            ("dead_end_outputs", "死端输出契约"), ("unfillable_inputs", "不可填输入契约"),
            ("unfillable_input_ports", "不可填输入端口"), ("seeds", "种子点"),
            ("pure_seed_nodes", "纯 seed 节点"), ("reusable_contracts", "可复用契约"),
        ):
            print(f"  {label:<16} {current[key]}")
        print(f"  {'分量规模':<16} {current['component_sizes'][:8]}")

        # islands, explained by role.  Printed in text mode only: the JSON shape
        # is what --baseline compares, and it must stay stable.
        islands = report["islands"]
        if islands["present"]:
            print()
            print(f"## 孤岛解释（{islands['total']} 个，每个都有分类依据）")
            for entry in islands["present"]:
                print(f"  {entry['label']:<24} {entry['count']}")
            for category in ("missing_upstream", "unreviewed"):
                flagged = [i for i in islands["items"] if i["category"] == category]
                if flagged:
                    print(f"  ── {flagged[0]['category_zh']} 明细：")
                    for island in flagged[:15]:
                        print(f"     {island['plugin_id']}  ({island['detail']})")
                    if len(flagged) > 15:
                        print(f"     … 另有 {len(flagged) - 15} 个")

    if args.baseline is not None:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        mismatch = _edge_set_mismatch(baseline, current)
        if mismatch is not None:
            print(mismatch, file=sys.stderr)
            return 2
        if not args.json:
            print()
            print("## 与基线对比")
            print()
            print(format_metrics_table(_as_metrics(baseline), metrics))
        problems = _regressions(baseline, current)
        if problems:
            print("\n连通性回归：", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        if not args.json:
            print("\n无回归。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
