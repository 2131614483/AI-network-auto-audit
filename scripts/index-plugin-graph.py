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
from pathlib import Path

import psycopg2

# 以 `python scripts/index-plugin-graph.py` 直接运行时，sys.path[0] 是 scripts/ 而不是
# 仓库根，`packages` 就导入不到。开发机上之所以没暴露，是因为仓库里的 .venv 做过
# editable 安装（根目录有 audit_network.egg-info）；按 requirements.txt 新装的干净环境
# 没有这层，会直接 ModuleNotFoundError。这里显式补上仓库根，让脚本不依赖安装方式。
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from packages.graph.plugin_indexer import IndexResult, index_domain  # noqa: E402
from packages.plugin_topology.blueprint_indexer import (  # noqa: E402
    BlueprintIndexResult,
    index_blueprints,
)

DEFAULT_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network"
)
DEFAULT_DOMAINS = ("audit",)

_TOTALS_SQL = """
SELECT
  (SELECT count(*) FROM graph.nodes WHERE node_type = 'capability'),
  (SELECT count(*) FROM graph.nodes),
  (SELECT count(*) FROM topology.plugin_blueprints)
"""


def _print_totals(database_url: str, tenant_slug: str, failures: int) -> None:
    """读回该租户库内的全量规模，供与部署文档的期望数字核对。

    按域的行只说明这一遍写了什么；文档里的「插件 / 图谱节点 / 蓝图」是数据库
    的状态，而且这几张表都是 RLS 强制隔离，必须设好 ``app.tenant_id`` 再数。
    """
    note = f"  （{failures} 个域失败，其余仍已写库）" if failures else ""
    try:
        with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
            row = cur.fetchone()
            if row is None:
                print(f"  库内合计     跳过：租户 {tenant_slug} 不存在", file=sys.stderr)
                return
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(row[0]),))
            cur.fetchone()
            cur.execute(_TOTALS_SQL)
            plugins, nodes, blueprints = cur.fetchone()
    except psycopg2.Error as exc:
        print(f"  库内合计     跳过：{type(exc).__name__}: {exc}", file=sys.stderr)
        return
    print(f"  库内合计     插件 {plugins}  图谱节点 {nodes}  蓝图 {blueprints}{note}")


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
    succeeded = 0
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
        except (ValueError, OSError, psycopg2.Error) as exc:
            print(f"  {domain:10s} 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
            continue
        _print_result(graph, blueprints, time.monotonic() - started)
        succeeded += 1

    if succeeded:
        _print_totals(args.database_url, args.tenant_slug, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
