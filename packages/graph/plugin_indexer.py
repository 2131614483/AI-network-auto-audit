"""插件目录 → 知识图谱：建图流水线（S1）。

为什么要它：M10 已经把"业务意图 → pg_trgm 打分 → 有界展开"这套图谱召回做出来了
（``packages/graph/graph_planning.py``），但图谱里只有 3 个手工插入的 legacy 槽位，
而磁盘上有 100+ 插件 —— 图与目录完全脱节，所以召回永远命中不到真实插件。
本模块负责把目录灌进图里。

三层节点（沿用既有的分层空间，不新造结构）：

===============  ==================  ==========================================
空间              节点类型             来源
===============  ==================  ==========================================
``<domain>-l3``  ``domain``          ``pack.json`` 的域
``capability-l2`` ``capability_family`` ``pack.stages``（s1–s8）+ 支持层/治理层
``capability-l2`` ``capability``      插件 manifest（``discover_plugins``）
===============  ==================  ==========================================

三类边：

- 域 → 能力：``bridge_edges`` / ``capability_contract``。跨空间，因此必须走桥；
  关系类型复用白名单里的 ``capability_contract``（``graph.validate_registered_bridge``
  会在提交时要求存在 active 的 ``bridge_rules``，这里与桥边在同一事务内注册）。
- 族 → 能力：``graph.edges`` / ``contains``。同空间。
- 能力 → 能力：``graph.edges`` / ``depends_on``。同空间，由
  ``connectivity.contract_edges`` **推导**（端口 ``contract_id`` 相等），
  而不是另写一张边表 —— 否则图与编译器会对"什么能连什么"产生分歧。

为 10 万+ 插件规模而定的三条硬约束：

1. **批量写入**：一次建图只建立常数条连接，用 ``execute_values`` 分批提交。
   ``GraphService.upsert_node`` 每次调用开一条连接，107 个插件就是 107 条，
   10 万级不可行，所以这里不循环调用它。
2. **幂等**：所有写入都是 ``ON CONFLICT``，重复建图不产生重复行。
3. **租户隔离**：``graph`` 的表是 ``FORCE ROW LEVEL SECURITY``，全程在
   ``app.tenant_id`` 会话变量下写入。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg2
from psycopg2.extensions import connection as _Connection
from psycopg2.extras import Json, execute_values

CAPABILITY_SPACE = "capability-l2"
"""Shared L2 space holding every domain's capability and family nodes."""

DOMAIN_SPACE_SUFFIX = "-l3"
"""``audit`` -> ``audit-l3``: matches the space the M10 migration already created."""

_DOMAIN_LEVEL = "L3"
_CAPABILITY_LEVEL = "L2"
_ROLE_BY_LEVEL = {_DOMAIN_LEVEL: "domain", _CAPABILITY_LEVEL: "capability"}

_BRIDGE_RELATION = "capability_contract"
_CONTAINS_RELATION = "contains"
_DEPENDS_ON_RELATION = "depends_on"

_FAMILY_LABEL = {"base": "基础支撑层", "gov": "治理层"}

_DEFAULT_BATCH = 1000
_DESCRIPTION_LIMIT = 200


@dataclass(frozen=True, slots=True)
class IndexResult:
    """What one pass wrote.  Counts are totals in the graph, not deltas."""

    domain: str
    spaces: int
    domains: int
    families: int
    capabilities: int
    aliases: int
    contains_edges: int
    depends_on_edges: int
    bridges: int
    blueprint_links: int


def _connect(database_url: str) -> _Connection:
    """The single connection seam, so the connection budget is testable."""
    return psycopg2.connect(database_url)


# --------------------------------------------------------------------------
# statements
# --------------------------------------------------------------------------

_SPACE_SQL = """
INSERT INTO graph.spaces(tenant_id, key, level, name) VALUES(%s, %s, %s, %s)
ON CONFLICT(tenant_id, key) DO UPDATE SET name = EXCLUDED.name, level = EXCLUDED.level
RETURNING id
"""

_SPACE_PROFILE_SQL = """
INSERT INTO graph.space_profiles(space_id, tenant_id, graph_role, cluster_key, description)
VALUES(%s, %s, %s, %s, %s)
ON CONFLICT(space_id) DO UPDATE SET graph_role = EXCLUDED.graph_role,
  cluster_key = EXCLUDED.cluster_key, description = EXCLUDED.description, updated_at = now()
"""

# `label` is filled in only when it is empty: the M10 migration hand-authored
# Chinese labels for the shipped blueprints ("总账质量校验"), and an indexer that
# overwrites them silently degrades recall — `match_nodes` reports
# ``match_kind="alias"`` as soon as an alias outscores the label, and the
# reviewer's wording (the one an operator actually types) is gone.  The plugin's
# own name is still recorded, under ``properties.name``.
_NODE_SQL = """
INSERT INTO graph.nodes(tenant_id, space_id, canonical_key, node_type, label, properties)
VALUES %s
ON CONFLICT(space_id, canonical_key) DO UPDATE
SET label = COALESCE(NULLIF(graph.nodes.label, ''), EXCLUDED.label),
    node_type = EXCLUDED.node_type,
    properties = EXCLUDED.properties,
    row_version = graph.nodes.row_version + 1,
    deleted_at = NULL
"""

_ALIAS_SQL = """
INSERT INTO graph.node_aliases(tenant_id, node_id, alias, alias_type, confidence)
VALUES %s
ON CONFLICT(node_id, alias) DO NOTHING
"""

_EDGE_SQL = """
INSERT INTO graph.edges(tenant_id, space_id, source_node_id, target_node_id, relation_type, weight)
VALUES %s
ON CONFLICT(space_id, source_node_id, target_node_id, relation_type)
DO UPDATE SET weight = EXCLUDED.weight, valid_to = NULL
"""

_BRIDGE_RULE_SQL = """
INSERT INTO graph.bridge_rules(
  tenant_id, source_space_id, target_space_id, relation_type, status, max_weight)
VALUES(%s, %s, %s, %s, 'active', 1)
ON CONFLICT(tenant_id, source_space_id, target_space_id, relation_type)
DO UPDATE SET status = 'active', updated_at = now()
"""

_BRIDGE_SQL = """
INSERT INTO graph.bridge_edges(
  tenant_id, source_space_id, target_space_id, source_node_id, target_node_id,
  relation_type, weight, status)
VALUES %s
ON CONFLICT(source_node_id, target_node_id, relation_type)
DO UPDATE SET weight = EXCLUDED.weight, status = 'active'
"""

_BLUEPRINT_LINK_SQL = """
INSERT INTO topology.blueprint_graph_links(
  tenant_id, blueprint_key, graph_space_key, node_key, relation)
VALUES %s
ON CONFLICT DO NOTHING
"""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _set_tenant(cur: Any, tenant_slug: str) -> Any:
    cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"tenant not found: {tenant_slug}")
    tenant_id = row[0]
    cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
    cur.fetchone()
    return tenant_id


def _ensure_space(
    cur: Any, tenant_id: Any, key: str, level: str, name: str, description: str,
) -> Any:
    role = _ROLE_BY_LEVEL[level]
    cur.execute(_SPACE_SQL, (tenant_id, key, level, name))
    space_id = cur.fetchone()[0]
    cur.execute(_SPACE_PROFILE_SQL, (space_id, tenant_id, role, key, description))
    return space_id


def _upsert_nodes(cur: Any, rows: Sequence[tuple[Any, ...]], batch_size: int) -> None:
    if rows:
        execute_values(
            cur, _NODE_SQL, rows, template="(%s,%s,%s,%s,%s,%s)", page_size=batch_size,
        )


def _node_ids(cur: Any, space_ids: Sequence[Any], keys: Sequence[str]) -> dict[str, Any]:
    """Resolve ``canonical_key -> id`` by reading the rows back.

    Deliberately a separate SELECT rather than ``INSERT ... RETURNING``: with a
    multi-row ``execute_values`` the RETURNING order is **not** guaranteed to
    match the input order (observed on this database — with ``ON CONFLICT DO
    UPDATE`` every edge came out attached to the wrong node), and zipping the
    two lists then silently mis-wires the whole graph.  Reading back is one
    extra round trip and removes the failure mode entirely.
    """
    if not keys or not space_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(space_ids))
    cur.execute(
        f"SELECT canonical_key, id FROM graph.nodes "
        f"WHERE space_id IN ({placeholders}) AND canonical_key = ANY(%s)",
        (*space_ids, list(keys)),
    )
    return {str(key): node_id for key, node_id in cur.fetchall()}


def _upsert_aliases(cur: Any, rows: Sequence[tuple[Any, ...]], batch_size: int) -> None:
    if rows:
        execute_values(cur, _ALIAS_SQL, rows, template="(%s,%s,%s,%s,%s)", page_size=batch_size)


def _sync_edges(
    cur: Any,
    *,
    tenant_id: Any,
    space_id: Any,
    relation_type: str,
    scope_keys: Sequence[str],
    expected: Sequence[tuple[str, str, Any, Any]],
    batch_size: int,
    retire_stale: bool = True,
) -> None:
    """Bring one relation up to date for ``scope_keys``: retire stale, then upsert.

    ``scope_keys`` are the *target* node keys this pass owns, so edges pointing
    outside the domain are left alone.

    ``retire_stale`` is per-relation and must be chosen deliberately:

    * ``contains`` is **owned** by this indexer — "which stage does this plugin
      belong to" is a pure derivation from ``pack.classify`` — so anything not
      in ``expected`` is genuinely stale and gets expired.
    * ``depends_on`` is **not**.  The M10 migration hand-authored semantic
      dependencies between the shipped blueprints, and those do not appear in
      ``contract_edges`` (they are governance statements, not port
      compatibility).  Expiring everything outside the derivation silently
      deleted them, which showed up as a planner that could no longer expand
      from ``audit.ledger.validate`` to ``audit.finding.draft``.  So this
      relation is add-only.

    Retiring is done with ``valid_to`` rather than ``DELETE`` because
    ``audit_app`` holds no DELETE grant and the project retires history instead
    of removing it; every reader already filters on ``valid_to IS NULL``.
    """
    if retire_stale:
        expected_pairs = {(s, t) for s, t, _si, _ti in expected}
        cur.execute(
            """SELECT e.id, src.canonical_key, tgt.canonical_key
               FROM graph.edges e
               JOIN graph.nodes src ON src.id = e.source_node_id
               JOIN graph.nodes tgt ON tgt.id = e.target_node_id
               WHERE e.space_id = %s AND e.relation_type = %s
                 AND tgt.canonical_key = ANY(%s) AND e.valid_to IS NULL""",
            (space_id, relation_type, list(scope_keys)),
        )
        stale_ids = [
            row[0] for row in cur.fetchall()
            if (str(row[1]), str(row[2])) not in expected_pairs
        ]
        if stale_ids:
            cur.execute("UPDATE graph.edges SET valid_to = now() WHERE id = ANY(%s)", (stale_ids,))

    if expected:
        execute_values(
            cur, _EDGE_SQL,
            [(tenant_id, space_id, source_id, target_id, relation_type, 1.0)
             for _sk, _tk, source_id, target_id in expected],
            template="(%s,%s,%s,%s,%s,%s)", page_size=batch_size,
        )


def _family_key(pack: Any, plugin_id: str) -> str | None:
    """Which family a plugin belongs to: a stage for business plugins, else a layer.

    ``pack`` is annotated ``Any`` because ``DomainPack`` lives in
    ``packages.ai_planner``, which this module imports lazily to keep the
    import graph one-way; the ``str()`` calls are what keep the declared return
    type honest.
    """
    layer, stage = pack.classify(plugin_id)
    if layer == "biz":
        return str(stage) if stage else None
    if layer == "base":
        segment = pack.support_segment
        return str(segment) if segment else None
    if layer == "gov":
        segment = pack.governance_segment
        return str(segment) if segment else None
    return None


def _aliases_for(spec: Any) -> tuple[tuple[str, str], ...]:
    """Aliases that make the node reachable from a human's wording.

    The Chinese ``name`` is what an operator actually types; the full capability
    and its tail serve English/debug queries.  Deliberately short — every alias
    is a recall candidate, and padding the table with description fragments
    makes trigram ranking worse, not better.
    """
    names = [(spec.name, "name")] if spec.name else []
    names.append((spec.capability, "capability"))
    tail = spec.capability.rsplit(".", 1)[-1]
    if tail and tail != spec.capability:
        names.append((tail, "token"))
    return tuple(names)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def index_domain(
    database_url: str,
    domain: str,
    *,
    tenant_slug: str = "local-dev",
    batch_size: int = _DEFAULT_BATCH,
) -> IndexResult:
    """Build the graph index for one domain pack.  Idempotent.

    Raises ``ValueError`` for an unknown domain, or one whose pack discovers no
    plugins — an empty index would look like "this domain has nothing", which is
    the failure mode ``load_pack`` already refuses to hide.
    """
    from packages.ai_planner.composer import discover_plugins
    from packages.ai_planner.connectivity import contract_edges
    from packages.ai_planner.domain_pack import load_pack

    pack = load_pack(domain)
    specs = discover_plugins(domain=domain)
    if not specs:
        raise ValueError(f"domain {domain!r} discovered no plugins to index")

    capability_space_key = CAPABILITY_SPACE
    domain_space_key = f"{domain}{DOMAIN_SPACE_SUFFIX}"

    # -- node rows, computed before opening the transaction -----------------
    domain_nodes = [(f"domain:{domain}", "domain", pack.name_zh or domain)]
    family_nodes: dict[str, tuple[str, str]] = {
        stage.key: (stage.name_zh or stage.key, stage.key) for stage in pack.stages
    }
    for layer, label in _FAMILY_LABEL.items():
        segment = pack.support_segment if layer == "base" else pack.governance_segment
        if segment:
            family_nodes[str(segment)] = (label, str(segment))

    capability_properties: dict[str, dict[str, Any]] = {}
    capability_labels: dict[str, str] = {}
    family_of: dict[str, str | None] = {}
    aliases_of: dict[str, tuple[tuple[str, str], ...]] = {}

    for plugin_id, spec in sorted(specs.items()):
        key = f"capability:{spec.capability}"
        layer, stage = pack.classify(plugin_id)
        family = _family_key(pack, plugin_id)
        family_of[key] = family
        capability_labels[key] = spec.name or spec.capability
        capability_properties[key] = {
            "capability": spec.capability,
            "plugin_id": plugin_id,
            "name": spec.name,
            "layer": layer,
            "stage": stage,
            "family": family,
            "lifecycle": spec.lifecycle,
            "domains": list(spec.domains),
            "inputs": [port.port_id for port in spec.inputs],
            "outputs": [port.port_id for port in spec.outputs],
            "description": (spec.description or "")[:_DESCRIPTION_LIMIT],
        }
        aliases_of[key] = _aliases_for(spec)

    # -- write, in one transaction ------------------------------------------
    with _connect(database_url) as connection, connection.cursor() as cur:
        tenant_id = _set_tenant(cur, tenant_slug)
        domain_space = _ensure_space(
            cur, tenant_id, domain_space_key, _DOMAIN_LEVEL,
            f"{pack.name_zh or domain}业务域", f"域 {domain} 的业务域节点空间",
        )
        capability_space = _ensure_space(
            cur, tenant_id, capability_space_key, _CAPABILITY_LEVEL,
            "能力语义空间", "按域灌入的插件能力与能力族节点",
        )

        node_rows = (
            [(tenant_id, domain_space, key, "domain", label, Json({"domain": domain}))
             for key, _t, label in domain_nodes]
            + [(tenant_id, capability_space, f"capability_family:{key}", "capability_family",
                label, Json({"family": key, "domain": domain}))
               for key, (label, _segment) in sorted(family_nodes.items())]
            + [(tenant_id, capability_space, key, "capability", capability_labels[key],
                Json(capability_properties[key]))
               for key in sorted(capability_labels)]
        )
        _upsert_nodes(cur, node_rows, batch_size)
        id_by_key = _node_ids(cur, [domain_space, capability_space], [row[2] for row in node_rows])

        alias_rows = [
            (tenant_id, id_by_key[key], alias, alias_type, 1.0)
            for key in sorted(aliases_of)
            for alias, alias_type in aliases_of[key]
            if key in id_by_key
        ]
        _upsert_aliases(cur, alias_rows, batch_size)

        # family -> capability (same space, plain edges)
        expected_contains = [
            (f"capability_family:{family_of[key]}", key,
             id_by_key[f"capability_family:{family_of[key]}"], id_by_key[key])
            for key in sorted(family_of)
            if family_of[key] and f"capability_family:{family_of[key]}" in id_by_key
            and key in id_by_key
        ]
        _sync_edges(
            cur, tenant_id=tenant_id, space_id=capability_space,
            relation_type=_CONTAINS_RELATION, scope_keys=sorted(capability_labels),
            expected=expected_contains, batch_size=batch_size,
        )

        # capability -> capability, derived from port-contract compatibility
        capability_by_plugin = {pid: spec.capability for pid, spec in specs.items()}
        expected_depends = []
        for producer, consumer in sorted(contract_edges(specs)):
            source_key = f"capability:{capability_by_plugin[producer]}"
            target_key = f"capability:{capability_by_plugin[consumer]}"
            if source_key in id_by_key and target_key in id_by_key:
                expected_depends.append(
                    (source_key, target_key, id_by_key[source_key], id_by_key[target_key])
                )
        _sync_edges(
            cur, tenant_id=tenant_id, space_id=capability_space,
            relation_type=_DEPENDS_ON_RELATION, scope_keys=sorted(capability_labels),
            expected=expected_depends, batch_size=batch_size, retire_stale=False,
        )

        # domain -> capability across spaces: a bridge, which needs its rule
        domain_node_key = f"domain:{domain}"
        cur.execute(
            _BRIDGE_RULE_SQL,
            (tenant_id, domain_space, capability_space, _BRIDGE_RELATION),
        )
        bridge_rows = [
            (tenant_id, domain_space, capability_space, id_by_key[domain_node_key],
             id_by_key[key], _BRIDGE_RELATION, 1.0, "active")
            for key in sorted(capability_labels)
            if key in id_by_key and domain_node_key in id_by_key
        ]
        if bridge_rows:
            execute_values(cur, _BRIDGE_SQL, bridge_rows,
                           template="(%s,%s,%s,%s,%s,%s,%s,%s)", page_size=batch_size)

        # shipped blueprints keep a link back to their node
        cur.execute(
            """SELECT key, capability_contract->>'capability' FROM topology.plugin_blueprints
               WHERE status IN ('active','planned') ORDER BY key"""
        )
        link_rows = [
            (tenant_id, str(blueprint_key), capability_space_key, f"capability:{capability}", "capability")
            for blueprint_key, capability in cur.fetchall()
            if capability and f"capability:{capability}" in id_by_key
        ]
        if link_rows:
            execute_values(cur, _BLUEPRINT_LINK_SQL, link_rows,
                           template="(%s,%s,%s,%s,%s)", page_size=batch_size)

        return IndexResult(
            domain=domain,
            spaces=2,
            domains=len(domain_nodes),
            families=len(family_nodes),
            capabilities=len(capability_labels),
            aliases=len(alias_rows),
            contains_edges=len(expected_contains),
            depends_on_edges=len(expected_depends),
            bridges=len(bridge_rows),
            blueprint_links=len(link_rows),
        )
