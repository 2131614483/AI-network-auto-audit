"""插件目录 → 组网蓝图目录：让「图谱召回」真正能变成计划。

为什么需要它：``GraphPlanningService`` 把召回结果翻译成组网需求的路径是
``blueprint_graph_links`` → ``plugin_blueprints``。S1 把 107 个插件灌进了图谱，
但这两张表里仍然只有 3 个 legacy 槽位 —— 于是召回出的真实插件
``capability_requirements`` 恒为空，``plan_from_intent`` **fail-closed**。
图谱能"看见"插件 ≠ 组网能"用上"插件，这一步补的就是中间那一环。

写入四张治理表（全部幂等）：

======================  ==========================================
表                       写什么
======================  ==========================================
``plugin_clusters``      每个域一个 ``business_domain`` 集群
``plugin_blueprints``    每个插件一行（``planned`` / ``capability``）
``cluster_memberships``  插件 → 集群（axis=``capability``）
``topology_edges``       blueprint 之间的 ``depends_on``，由端口 contract 兼容性推导
``blueprint_graph_links`` blueprint ↔ 图谱节点（``capability:<name>``）
======================  ==========================================

四条硬约束（都来自表上的 CHECK / UNIQUE，或踩过才知道）：

1. ``plugin_blueprints.status`` 只允许 ``planned`` / ``released`` / ``archived``
   —— 而 ``TopologyService._load_catalog`` 只读 ``planned`` / ``released``。
   写 ``active`` 会被 CHECK 拒绝；即便放过，planner 也看不见它。
2. **每个 blueprint 必须至少属于一个集群**：``TopologyPlanner.plan`` 对
   ``cluster_memberships`` 为空的候选**直接跳过**，所以漏了这一步的插件等于没注册。
3. ``blueprint_graph_links`` 有指向 ``plugin_blueprints(tenant_id, key)`` 的外键，
   所以**必须先建 blueprint 再建 link**，顺序不能反。
4. **只注册 ``verified`` 插件**。这张目录是 planner *选中并执行*的地方，而
   ``contract_only`` 插件只有契约、没有可运行实现 —— 注册进去会让计划物化成一条
   跑不起来的链。实测过：全量注册后 planner 按字典序选了
   ``audit.ledger-quality``（``contract_only``）而放弃可执行的 ``ledger-quality-slot``，
   隔离执行随即失败。图谱那边（``graph/plugin_indexer.py``）**刻意保留全量契约**——
   "知道接口"和"能跑"是两件事。

与 ``packages/graph/plugin_indexer.py``（插件 → 图谱）是姊妹模块：一个负责让图谱
"知道"插件，一个负责让组网"选得中"插件。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg2
from psycopg2.extensions import connection as _Connection
from psycopg2.extras import Json, execute_values

#: `plugin_blueprints.status` CHECK: planned | released | archived.
#: `planned` is the one `_load_catalog` reads and that a live plugin belongs to.
_BLUEPRINT_STATUS = "planned"
#: `plugin_blueprints.blueprint_type` CHECK: capability | producer | consumer.
_BLUEPRINT_TYPE = "capability"
_BLUEPRINT_VERSION = "1.0.0"

#: Only ``verified`` plugins are registered.  The blueprint catalog is what the
#: planner *selects from and then executes*, so a ``contract_only`` plugin must
#: not be in it: it declares a contract but ships no runnable implementation, and
#: a plan that picks it materializes into a chain that cannot run.  (Observed
#: exactly that: registering everything made the planner choose
#: ``audit.ledger-quality`` — ``contract_only`` — over the shipped, executable
#: ``ledger-quality-slot``, and the isolated run failed.)  The graph index in
#: ``packages/graph/plugin_indexer.py`` deliberately keeps the *full* contract
#: directory; knowing a plugin's interface is not the same as being able to run it.
_REGISTERED_LIFECYCLE = "verified"

_CLUSTER_KIND = "business_domain"
_CLUSTER_STATUS = "active"
#: `cluster_memberships.axis` CHECK: business | capability | resource | governance.
_MEMBERSHIP_AXIS = "capability"

_EDGE_RELATION = "depends_on"
_EDGE_STATUS = "active"
_EDGE_WEIGHT = 1.0

# Same shape the shipped `business-financial-audit` cluster uses.
_CLUSTER_BUDGET = {"max_candidates": 8, "max_latency_ms": 5000, "max_chain_length": 4}
_CLUSTER_BRIDGES = ["artifact_ref", "capability_contract"]

_GRAPH_SPACE = "capability-l2"

_DEFAULT_BATCH = 1000


@dataclass(frozen=True, slots=True)
class BlueprintIndexResult:
    domain: str
    cluster_key: str
    clusters: int
    blueprints: int
    memberships: int
    edges: int
    links: int
    retired: int = 0


def _connect(database_url: str) -> _Connection:
    """The single connection seam, so the connection budget stays testable."""
    return psycopg2.connect(database_url)


_CLUSTER_SQL = """
INSERT INTO topology.plugin_clusters(
  tenant_id, key, name, cluster_kind, description, routing_budget, allowed_bridge_kinds, status)
VALUES(%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT(tenant_id, key) DO UPDATE SET
  name = EXCLUDED.name, cluster_kind = EXCLUDED.cluster_kind,
  description = EXCLUDED.description, routing_budget = EXCLUDED.routing_budget,
  allowed_bridge_kinds = EXCLUDED.allowed_bridge_kinds, updated_at = now()
RETURNING id
"""

_BLUEPRINT_SQL = """
INSERT INTO topology.plugin_blueprints(
  tenant_id, primary_cluster_id, key, name, blueprint_type, capability_contract,
  source_refs, status)
VALUES %s
ON CONFLICT(tenant_id, key) DO UPDATE SET
  primary_cluster_id = EXCLUDED.primary_cluster_id, name = EXCLUDED.name,
  blueprint_type = EXCLUDED.blueprint_type, capability_contract = EXCLUDED.capability_contract,
  source_refs = EXCLUDED.source_refs, status = EXCLUDED.status, updated_at = now()
"""

_MEMBERSHIP_SQL = """
INSERT INTO topology.cluster_memberships(tenant_id, blueprint_id, cluster_id, axis)
VALUES %s
ON CONFLICT(tenant_id, blueprint_id, cluster_id) DO NOTHING
"""

_EDGE_SQL = """
INSERT INTO topology.topology_edges(
  tenant_id, source_blueprint_id, target_blueprint_id, relation_type, weight, status)
VALUES %s
ON CONFLICT(tenant_id, source_blueprint_id, target_blueprint_id, relation_type)
DO UPDATE SET weight = EXCLUDED.weight, status = EXCLUDED.status, updated_at = now()
"""

_LINK_SQL = """
INSERT INTO topology.blueprint_graph_links(
  tenant_id, blueprint_key, graph_space_key, node_key, relation)
VALUES %s
ON CONFLICT(tenant_id, blueprint_key, node_key) DO NOTHING
"""


def _set_tenant(cur: Any, tenant_slug: str) -> Any:
    cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"tenant not found: {tenant_slug}")
    tenant_id = row[0]
    cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
    cur.fetchone()
    return tenant_id


def _ensure_cluster(cur: Any, tenant_id: Any, key: str, name: str, description: str) -> Any:
    cur.execute(
        _CLUSTER_SQL,
        (tenant_id, key, name, _CLUSTER_KIND, description,
         Json(_CLUSTER_BUDGET), Json(_CLUSTER_BRIDGES), _CLUSTER_STATUS),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("cluster upsert returned no id")
    return row[0]


def _blueprint_ids(cur: Any, tenant_id: Any, keys: Sequence[str]) -> dict[str, Any]:
    """Resolve ``key -> id`` by reading the rows back.

    Not ``INSERT ... RETURNING``: with a multi-row statement the RETURNING order
    is not guaranteed to match the input order, and zipping the two lists
    silently mis-wires every id derived from it (the same trap S1 hit when
    indexing the graph).
    """
    if not keys:
        return {}
    cur.execute(
        "SELECT key, id FROM topology.plugin_blueprints WHERE tenant_id=%s AND key = ANY(%s)",
        (tenant_id, list(keys)),
    )
    return {str(key): blueprint_id for key, blueprint_id in cur.fetchall()}


def index_blueprints(
    database_url: str,
    domain: str,
    *,
    tenant_slug: str = "local-dev",
    batch_size: int = _DEFAULT_BATCH,
) -> BlueprintIndexResult:
    """Register one domain's plugins into the topology blueprint catalog.  Idempotent.

    Raises ``ValueError`` for an unknown domain, or one whose pack discovers no
    plugins — an empty catalog would make the planner fail closed for reasons
    that look like "no such capability" rather than "nothing was registered".
    """
    from packages.ai_planner.composer import discover_plugins
    from packages.ai_planner.connectivity import contract_edges
    from packages.ai_planner.domain_pack import load_pack

    pack = load_pack(domain)
    cluster_key = f"business-{domain}"
    specs = discover_plugins(domain=domain, lifecycle=_REGISTERED_LIFECYCLE)
    if not specs:
        # A domain may legitimately ship contracts only — aiops / quant /
        # knowledge all do today.  Those plugins belong in the graph (their
        # interfaces are real) but not here, because nothing can run them.
        # Empty is the honest answer, not an error.
        return BlueprintIndexResult(
            domain=domain, cluster_key=cluster_key, clusters=0,
            blueprints=0, memberships=0, edges=0, links=0, retired=0,
        )

    cluster_name = f"{pack.name_zh or domain}业务域"

    with _connect(database_url) as connection, connection.cursor() as cur:
        tenant_id = _set_tenant(cur, tenant_slug)
        cluster_id = _ensure_cluster(
            cur, tenant_id, cluster_key, cluster_name,
            f"由插件目录建图流水线登记的 {domain} 域集群",
        )

        blueprint_rows = [
            (
                tenant_id, cluster_id, plugin_id, spec.name or plugin_id, _BLUEPRINT_TYPE,
                Json({
                    "capability": spec.capability,
                    "version": _BLUEPRINT_VERSION,
                    "inputs": [port.port_id for port in spec.inputs],
                    "outputs": [port.port_id for port in spec.outputs],
                    "primary_cluster_key": cluster_key,
                }),
                Json([{"uri": f"plugin:{plugin_id}", "kind": "plugin_protocol"}]),
                _BLUEPRINT_STATUS,
            )
            for plugin_id, spec in sorted(specs.items())
        ]
        execute_values(
            cur, _BLUEPRINT_SQL, blueprint_rows,
            template="(%s,%s,%s,%s,%s,%s,%s,%s)", page_size=batch_size,
        )

        id_by_key = _blueprint_ids(cur, tenant_id, sorted(specs))

        membership_rows = [
            (tenant_id, id_by_key[plugin_id], cluster_id, _MEMBERSHIP_AXIS)
            for plugin_id in sorted(specs)
            if plugin_id in id_by_key
        ]
        if membership_rows:
            execute_values(
                cur, _MEMBERSHIP_SQL, membership_rows,
                template="(%s,%s,%s,%s)", page_size=batch_size,
            )

        edge_rows = [
            (tenant_id, id_by_key[producer], id_by_key[consumer],
             _EDGE_RELATION, _EDGE_WEIGHT, _EDGE_STATUS)
            for producer, consumer in sorted(contract_edges(specs))
            if producer in id_by_key and consumer in id_by_key
        ]
        if edge_rows:
            execute_values(
                cur, _EDGE_SQL, edge_rows,
                template="(%s,%s,%s,%s,%s,%s)", page_size=batch_size,
            )

        link_rows = [
            (tenant_id, plugin_id, _GRAPH_SPACE, f"capability:{spec.capability}", "capability")
            for plugin_id, spec in sorted(specs.items())
            if plugin_id in id_by_key
        ]
        if link_rows:
            execute_values(
                cur, _LINK_SQL, link_rows,
                template="(%s,%s,%s,%s,%s)", page_size=batch_size,
            )

        # Retire blueprints in this cluster that are no longer registered — e.g. a
        # plugin that was demoted to `contract_only`, or one registered by an
        # earlier pass that did not filter on lifecycle.  Retired with `status`
        # rather than deleted: `audit_app` has no DELETE grant, and
        # `_load_catalog` only reads `planned`/`released`, so `archived` is
        # enough to make the planner stop offering an unrunnable blueprint.
        # Scoped to this cluster, which the indexer owns outright.
        cur.execute(
            """UPDATE topology.plugin_blueprints
               SET status = 'archived', updated_at = now()
               WHERE tenant_id = %s AND primary_cluster_id = %s
                 AND status <> 'archived' AND key <> ALL(%s)""",
            (tenant_id, cluster_id, sorted(specs)),
        )
        retired = int(cur.rowcount or 0)

        return BlueprintIndexResult(
            domain=domain,
            cluster_key=cluster_key,
            clusters=1,
            blueprints=len(blueprint_rows),
            memberships=len(membership_rows),
            edges=len(edge_rows),
            links=len(link_rows),
            retired=retired,
        )
