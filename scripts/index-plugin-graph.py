"""把插件目录灌进知识图谱与组网蓝图目录（S1 建图 + 蓝图注册）。

两件事一起做，因为它们是同一次"从目录到数据库"的投影，缺任何一半链路都断：

1. **图谱**（``graph/plugin_indexer.py``）—— 让召回"看得见"插件；
   收全量契约目录（含 ``contract_only``）。
2. **蓝图目录**（``plugin_topology/blueprint_indexer.py``）—— 让组网"选得中"插件；
   只收 ``verified`` 插件，因为 planner 选中之后要真的执行它。

只做 1 会得到"能召回但规划不了"，只做 2 会得到"能规划但召回不到"。

用法::

    .venv\\Scripts\\python.exe scripts\\index-plugin-graph.py
    .venv\\Scripts\\python.exe scripts\\index-plugin-graph.py --domain audit --domain aiops
    .venv\\Scripts\\python.exe scripts\\index-plugin-graph.py --database-url postgresql://audit_app:admin@localhost:5432/audit_network_test

幂等：重复执行不会产生重复节点 / 边 / 别名 / 蓝图，可以放进"插件目录变更后"的例行流程。
只读插件目录，写图谱与蓝图目录；不触碰插件协议、不执行任何插件。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from packages.graph.plugin_indexer import IndexResult, index_domain
from packages.plugin_topology.blueprint_indexer import BlueprintIndexResult, index_blueprints

DEFAULT_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network"
)
DEFAULT_DOMAINS = ("audit",)


def _print_result(graph: IndexResult, blueprints: BlueprintIndexResult, elapsed: float) -> None:
    print(
        f"  {graph.domain:10s} 图谱[域 {graph.domains} 族 {graph.families} 能力 {graph.capabilities} "
        f"别名 {graph.aliases} contains {graph.contains_edges} depends_on {graph.depends_on_edges} "
        f"桥 {graph.bridges}]  蓝图[集群 {blueprints.clusters} 蓝图 {blueprints.blueprints} "
        f"member {blueprints.memberships} 边 {blueprints.edges} 回连 {blueprints.links} "
        f"下架 {blueprints.retired}]  ({elapsed:.2f}s)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Index the plugin directory into the graph and the blueprint catalog (idempotent)."
    )
    parser.add_argument(
        "--database-url", default=DEFAULT_DATABASE_URL,
        help="target database; defaults to $DATABASE_URL",
    )
    parser.add_argument(
        "--domain", action="append", dest="domains", default=None,
        help="domain pack to index; repeatable (default: audit)",
    )
    parser.add_argument("--tenant-slug", default="local-dev", help="tenant slug")
    parser.add_argument("--batch-size", type=int, default=1000, help="rows per statement")
    args = parser.parse_args()

    domains = tuple(args.domains) if args.domains else DEFAULT_DOMAINS
    print(f"目标库 {args.database_url}")
    print(f"域 {', '.join(domains)} / 租户 {args.tenant_slug}")
    failures = 0
    for domain in domains:
        started = time.monotonic()
        try:
            graph = index_domain(
                args.database_url, domain,
                tenant_slug=args.tenant_slug, batch_size=args.batch_size,
            )
            blueprints = index_blueprints(
                args.database_url, domain,
                tenant_slug=args.tenant_slug, batch_size=args.batch_size,
            )
        except (ValueError, OSError) as exc:
            print(f"  {domain:10s} 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
            continue
        _print_result(graph, blueprints, time.monotonic() - started)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
