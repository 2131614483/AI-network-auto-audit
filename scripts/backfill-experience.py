"""把历史运行的执行事实回填进经验层（投影 → 观测 → 统计 → 候选）。

**为什么需要它**：经验投影器（``start_plan_run`` → ``project_run_best_effort``）是
2026-09-13 才接上 DAG 运行路径的，而本租户最后一次真实运行是 2026-09-12 —— 接线晚于
最后一次运行，于是四张经验表一直是 0 行，进化闭环的环 2/3/4/5/6 全部停在"机制在、
从未行使"。重跑一遍业务只为点亮看板既慢又没必要，本脚本直接把已有的 run 台账投影一次。

**为什么必须逐 run 调 ``project_run``，不能只调 ``rebuild``**：``rebuild`` 先清空统计表、
再从**已有观测**重建；观测为 0 时它产出 0。观测只能由 ``project_run`` 从
``control.node_attempts`` 现算出来。

用法::

    .venv\\Scripts\\python.exe scripts\\backfill-experience.py            # 只读试算，不写库
    .venv\\Scripts\\python.exe scripts\\backfill-experience.py --yes      # 真正回填
    .venv\\Scripts\\python.exe scripts\\backfill-experience.py --yes --tenant-slug local-dev

幂等：观测表是 append-only 且带 ``(tenant,run,source_instance,target_instance,contract)``
唯一约束，重复执行不会翻倍；候选表对 ``accepted``/``dismissed`` 冻结不复活。
只读运行台账，写经验四表；不执行插件、不触碰执行面与策略。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any
from uuid import UUID

import psycopg2
from psycopg2.extras import register_uuid

from packages.ai.env_store import load_env_file_once
from packages.experience.handoff import extract_handovers, extract_node_uses
from packages.experience.projector import ExperienceProjector
from packages.plugin_topology.dag_persistence import AttemptStore

register_uuid()  # type: ignore[no-untyped-call]  # run_id / trace_id 以 UUID 类型往返

DEFAULT_DATABASE_URL = "postgresql://audit_app:admin@localhost:5432/audit_network"


def _tenant_id(database_url: str, slug: str) -> UUID:
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (slug,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"tenant not found: {slug}")
    return UUID(str(row[0]))


def _runs_with_attempts(database_url: str, tenant_id: UUID) -> list[tuple[str, str]]:
    """(run_id, trace_id) 按首次 attempt 时间升序；回填顺序与运行顺序一致，便于对账。"""

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            """SELECT run_id, min(trace_id)::text
               FROM control.node_attempts
               WHERE tenant_id=%s
               GROUP BY run_id
               ORDER BY min(created_at)""",
            (tenant_id,),
        )
        return [(str(row[0]), str(row[1])) for row in cur.fetchall()]


def _dry_run(database_url: str, tenant_id: UUID, runs: list[tuple[str, str]]) -> dict[str, Any]:
    """只读试算：回填会写入多少观测、可能产出多少候选。不写任何表。"""

    declared = ExperienceProjector.declared_dataflow_edges()
    store = AttemptStore(database_url)
    node_total = edge_total = 0
    undeclared: set[tuple[str, str, str]] = set()
    empty: list[str] = []
    for run_id, _trace_id in runs:
        attempts = store.query_attempts(tenant_id=tenant_id, run_id=run_id)
        handovers = extract_handovers(attempts)
        node_uses = extract_node_uses(attempts)
        edge_keys = {handover.edge_key() for handover in handovers}
        node_total += len({(use.plugin_id, use.capability) for use in node_uses})
        edge_total += len(edge_keys)
        undeclared |= edge_keys - declared
        if not handovers and not node_uses:
            empty.append(run_id)
    return {
        "declared_edges": len(declared),
        "node_observations": node_total,
        "edge_observations": edge_total,
        "suggestion_upper_bound": len(undeclared),
        "runs_without_facts": empty,
    }


def _project_all(
    database_url: str, tenant_id: UUID, runs: list[tuple[str, str]]
) -> tuple[dict[str, int], list[tuple[str, str]]]:
    """逐 run 投影。``declared_edges`` 只算一次，避免每个 run 重建一遍星云图。"""

    projector = ExperienceProjector(database_url)
    declared = ExperienceProjector.declared_dataflow_edges()
    totals = {
        "handovers": 0, "node_uses": 0,
        "edge_observations_inserted": 0, "node_observations_inserted": 0,
        "edges_recomputed": 0, "nodes_recomputed": 0, "suggestions_upserted": 0,
    }
    failures: list[tuple[str, str]] = []
    for run_id, trace_id in runs:
        try:
            summary = projector.project_run(
                tenant_id=tenant_id, run_id=run_id, trace_id=trace_id, declared_edges=declared,
            )
        except Exception as exc:  # 单 run 失败不该中断整批；逐条列出由人来判断
            failures.append((run_id, f"{type(exc).__name__}: {exc}"))
            continue
        for key in totals:
            totals[key] += int(summary.get(key) or 0)
    return totals, failures


def _suggestion_rows(database_url: str, tenant_id: UUID) -> dict[str, int]:
    """本租户候选的**落库行数**，按状态分组。

    与 ``project_run`` 返回的 ``suggestions_upserted`` 不是一回事：后者是"写入或
    刷新"的**调用次数**，同一条未声明边在 N 个 run 里出现就会被计 N 次。
    """

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            "SELECT status, count(*) FROM experience.relation_suggestions WHERE tenant_id=%s GROUP BY status",
            (tenant_id,),
        )
        return {str(row[0]): int(row[1]) for row in cur.fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill the experience layer from historical runs (idempotent, projection only)."
    )
    parser.add_argument("--tenant-slug", default="local-dev", help="tenant slug")
    parser.add_argument(
        "--database-url",
        default=None,
        help="target database; defaults to $DATABASE_URL, then the local dev库",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="actually write; without it the script only does a read-only dry run",
    )
    args = parser.parse_args()

    # .env 作为默认值（override=False）：显式导出的进程变量优先，与 API 的加载语义一致。
    load_env_file_once()
    database_url = args.database_url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL

    tenant_id = _tenant_id(database_url, args.tenant_slug)
    runs = _runs_with_attempts(database_url, tenant_id)
    print(f"租户 {args.tenant_slug}（{tenant_id}）· 有 attempt 台账的 run：{len(runs)}")
    if not runs:
        print("没有可回填的 run：该租户的 control.node_attempts 为空。")
        return 0

    if not args.yes:
        started = time.monotonic()
        preview = _dry_run(database_url, tenant_id, runs)
        print(f"\n[试算·未写库] 设计态声明边 {preview['declared_edges']} 条")
        print(f"  预计写入 node_observations ≈ {preview['node_observations']}")
        print(f"  预计写入 edge_observations ≈ {preview['edge_observations']}")
        print(f"  候选（设计态未声明边）上限 {preview['suggestion_upper_bound']} 条")
        if preview["runs_without_facts"]:
            print(f"  无交接/无节点用途的 run：{len(preview['runs_without_facts'])} 个（仍会写 0 行，属正常）")
        print(f"  ({time.monotonic() - started:.1f}s)")
        print("\n加 --yes 才会真正写入经验四表。")
        return 0

    started = time.monotonic()
    totals, failures = _project_all(database_url, tenant_id, runs)
    elapsed = time.monotonic() - started
    print(f"\n[回填完成] {len(runs) - len(failures)}/{len(runs)} 个 run 投影成功 ({elapsed:.1f}s)")
    print(f"  交接 {totals['handovers']} · 节点用途 {totals['node_uses']}")
    print(f"  写入观测：边 {totals['edge_observations_inserted']} · 点 {totals['node_observations_inserted']}")
    print(f"  重算统计：边 {totals['edges_recomputed']} · 点 {totals['nodes_recomputed']}")
    by_status = _suggestion_rows(database_url, tenant_id)
    distinct = sum(by_status.values())
    detail = " · ".join(f"{status} {count}" for status, count in sorted(by_status.items())) or "无"
    print(f"  候选（relation_suggestions）：本租户现有 {distinct} 条（{detail}）")
    print(
        f"    其中本次写入/刷新调用 {totals['suggestions_upserted']} 次"
        "——同一条未声明边在多个 run 中出现会重复计数，故此数 ≥ 行数"
    )
    if failures:
        print(f"\n失败的 run（{len(failures)} 个）：", file=sys.stderr)
        for run_id, reason in failures:
            print(f"  {run_id}  {reason}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
