"""插件目录 → 知识图谱 的建图契约（S1：让图谱真正承载插件网络）。

Acceptance —— 这些用例锁定的是"建图"这件事本身，而不是某个插件的业务行为：

  - 一个域的全部插件都成为 L2 ``capability`` 节点，且落在 ``capability-l2`` 空间；
  - ``pack.json`` 的阶段（s1–s8）成为 ``capability_family`` 节点，这是多级展开
    里"族"那一层 —— 此前图谱里根本不存在；
  - 域节点落在 L3 空间，域→能力走**已注册**的 ``capability_contract`` 桥
    （不新增 relation_type，不绕过 ``graph.bridge_rules`` 触发器）；
  - 能力→能力的 ``depends_on`` 由端口 ``contract_id`` 相等**推导**，与
    ``connectivity.contract_edges`` 一致，而不是手写一张边表；
  - 中文别名入库，使 pg_trgm 意图召回能命中真实插件；
  - **幂等**：重复建图不产生重复节点 / 边 / 别名；
  - **批量写入**：一次建图的数据库连接数是常数（与插件数无关）——这是
    10 万+ 插件规模的前提，``GraphService.upsert_node`` 那种每节点一条连接的
    写法在 10 万级不可行。
"""

from __future__ import annotations

import os

import psycopg2
import pytest

from packages.ai_planner.connectivity import contract_edges
from packages.ai_planner.domain_pack import load_pack
from packages.ai_planner.workbench import _specs_for
from packages.graph.plugin_indexer import IndexResult, index_domain

TEST_DB = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or (
    "postgresql://audit_app:admin@localhost:5432/audit_network_test"
)
DOMAIN = "audit"
CAPABILITY_SPACE = "capability-l2"
DOMAIN_SPACE = "audit-l3"


def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(TEST_DB)


def _set_tenant(cur: object) -> None:
    cur.execute(  # type: ignore[attr-defined]
        "SELECT set_config('app.tenant_id', "
        "(SELECT id::text FROM iam.tenants WHERE slug='local-dev'), false)"
    )
    cur.fetchone()  # type: ignore[attr-defined]


def _node_keys(node_type: str, space_key: str) -> set[str]:
    with _connect() as connection, connection.cursor() as cur:
        _set_tenant(cur)
        cur.execute(
            """SELECT n.canonical_key FROM graph.nodes n
               JOIN graph.spaces s ON s.id = n.space_id
               WHERE s.key = %s AND n.node_type = %s AND n.deleted_at IS NULL""",
            (space_key, node_type),
        )
        return {str(row[0]) for row in cur.fetchall()}


def _node_aliases(canonical_key: str) -> set[str]:
    with _connect() as connection, connection.cursor() as cur:
        _set_tenant(cur)
        cur.execute(
            """SELECT a.alias FROM graph.node_aliases a
               JOIN graph.nodes n ON n.id = a.node_id
               WHERE n.canonical_key = %s""",
            (canonical_key,),
        )
        return {str(row[0]) for row in cur.fetchall()}


def _internal_edges(relation_type: str) -> set[tuple[str, str]]:
    """``(source_canonical_key, target_canonical_key)`` inside the capability space."""
    with _connect() as connection, connection.cursor() as cur:
        _set_tenant(cur)
        cur.execute(
            """SELECT src.canonical_key, tgt.canonical_key
               FROM graph.edges e
               JOIN graph.nodes src ON src.id = e.source_node_id
               JOIN graph.nodes tgt ON tgt.id = e.target_node_id
               JOIN graph.spaces s ON s.id = e.space_id
               WHERE s.key = %s AND e.relation_type = %s AND e.valid_to IS NULL""",
            (CAPABILITY_SPACE, relation_type),
        )
        return {(str(a), str(b)) for a, b in cur.fetchall()}


def _bridge_count(relation_type: str) -> int:
    with _connect() as connection, connection.cursor() as cur:
        _set_tenant(cur)
        cur.execute(
            """SELECT count(*) FROM graph.bridge_edges
               WHERE relation_type = %s AND status = 'active'""",
            (relation_type,),
        )
        return int(cur.fetchone()[0])


def _active_bridge_rule(source_space_key: str, target_space_key: str, relation_type: str) -> bool:
    with _connect() as connection, connection.cursor() as cur:
        _set_tenant(cur)
        cur.execute(
            """SELECT count(*) FROM graph.bridge_rules r
               JOIN graph.spaces s1 ON s1.id = r.source_space_id
               JOIN graph.spaces s2 ON s2.id = r.target_space_id
               WHERE s1.key = %s AND s2.key = %s AND r.relation_type = %s
                 AND r.status = 'active'""",
            (source_space_key, target_space_key, relation_type),
        )
        return int(cur.fetchone()[0]) > 0


def _blueprint_links() -> set[tuple[str, str]]:
    with _connect() as connection, connection.cursor() as cur:
        _set_tenant(cur)
        cur.execute("SELECT blueprint_key, node_key FROM topology.blueprint_graph_links")
        return {(str(a), str(b)) for a, b in cur.fetchall()}


def _graph_snapshot() -> tuple[int, int, int]:
    with _connect() as connection, connection.cursor() as cur:
        _set_tenant(cur)
        cur.execute(
            "SELECT (SELECT count(*) FROM graph.nodes WHERE deleted_at IS NULL),"
            "       (SELECT count(*) FROM graph.edges),"
            "       (SELECT count(*) FROM graph.node_aliases)"
        )
        row = cur.fetchone()
        assert row is not None
        return int(row[0]), int(row[1]), int(row[2])


# -- 1. the plugin directory becomes capability nodes ------------------------------


def test_every_domain_plugin_becomes_a_capability_node() -> None:
    result = index_domain(TEST_DB, DOMAIN)
    assert isinstance(result, IndexResult)
    assert result.domain == DOMAIN

    keys = _node_keys("capability", CAPABILITY_SPACE)
    specs = _specs_for(DOMAIN)
    assert specs, "audit pack must discover plugins"
    missing = {f"capability:{s.capability}" for s in specs.values()} - keys
    assert missing == set(), f"plugins present on disk but not in the graph: {sorted(missing)[:5]}"
    assert result.capabilities >= len(specs)


def test_the_family_layer_is_built() -> None:
    """The multi-level expansion needs a family layer between domain and plugin.

    It did not exist before: ``pack.json`` declares s1–s8 but nothing ever
    turned them into graph nodes, so a domain could only expand straight to
    plugins with no intermediate grouping.  The support and governance layers
    get a family each, because ``classify`` puts their plugins in no stage.
    """
    index_domain(TEST_DB, DOMAIN)
    families = _node_keys("capability_family", CAPABILITY_SPACE)
    pack = load_pack(DOMAIN)
    assert pack.stages
    for stage in pack.stages:
        assert f"capability_family:{stage.key}" in families
    assert f"capability_family:{pack.support_segment}" in families
    assert f"capability_family:{pack.governance_segment}" in families


def test_every_plugin_belongs_to_exactly_one_family() -> None:
    """``contains`` must cover the whole directory — no plugin left ungrouped."""
    index_domain(TEST_DB, DOMAIN)
    contains = _internal_edges("contains")
    pack = load_pack(DOMAIN)
    specs = _specs_for(DOMAIN)

    expected: set[tuple[str, str]] = set()
    for plugin_id, spec in specs.items():
        layer, stage = pack.classify(plugin_id)
        if layer == "biz":
            family = stage
        elif layer == "base":
            family = pack.support_segment
        else:
            family = pack.governance_segment
        if family:
            expected.add((f"capability_family:{family}", f"capability:{spec.capability}"))

    assert len(expected) == len(specs), "every discovered plugin must map to a family"
    missing = expected - contains
    assert missing == set(), f"plugins with no family edge: {sorted(missing)[:5]}"


def test_domain_node_lives_in_the_l3_space() -> None:
    index_domain(TEST_DB, DOMAIN)
    assert f"domain:{DOMAIN}" in _node_keys("domain", DOMAIN_SPACE)


# -- 2. edges are derived, not hand-written ----------------------------------------


def test_depends_on_edges_mirror_contract_compatibility() -> None:
    """``depends_on`` must equal what the port contracts already imply.

    ``connectivity.contract_edges`` derives ``(producer, consumer)`` from
    ``contract_id`` equality — the same relation the compiler uses to decide an
    edge is legal.  Re-deriving it by hand in the indexer would let the graph
    and the compiler disagree about what can connect to what.
    """
    index_domain(TEST_DB, DOMAIN)
    specs = _specs_for(DOMAIN)
    capability_by_plugin = {pid: spec.capability for pid, spec in specs.items()}
    expected = {
        (
            f"capability:{capability_by_plugin[producer]}",
            f"capability:{capability_by_plugin[consumer]}",
        )
        for producer, consumer in contract_edges(specs)
    }
    assert expected, "audit plugins must have contract-compatible pairs"

    actual = _internal_edges("depends_on")
    missing = expected - actual
    assert missing == set(), f"contract-compatible pairs missing from the graph: {sorted(missing)[:5]}"


def test_domain_to_capability_uses_the_registered_bridge() -> None:
    index_domain(TEST_DB, DOMAIN)
    assert _bridge_count("capability_contract") > 0
    assert _active_bridge_rule(DOMAIN_SPACE, CAPABILITY_SPACE, "capability_contract"), (
        "the bridge trigger requires an active bridge rule; register_bridge must have created it"
    )


# -- 3. aliases make Chinese intent recallable -------------------------------------


def test_capability_nodes_carry_chinese_aliases() -> None:
    index_domain(TEST_DB, DOMAIN)
    specs = _specs_for(DOMAIN)
    sample = specs["audit.evidence.workpaper-review3"]
    aliases = _node_aliases(f"capability:{sample.capability}")
    assert sample.name in aliases


def test_chinese_intent_matches_a_real_plugin() -> None:
    """The point of building the index: an operator's wording finds real plugins."""
    index_domain(TEST_DB, DOMAIN)
    from packages.graph.graph_planning import CapabilityGraphAdapter

    adapter = CapabilityGraphAdapter(TEST_DB)
    matches = adapter.match_nodes("底稿三级复核", max_matches=8)
    assert matches, "a plugin's own name must match its node"
    keys = {m["node_key"] for m in matches}
    specs = _specs_for(DOMAIN)
    assert f"capability:{specs['audit.evidence.workpaper-review3'].capability}" in keys


# -- 4. idempotence and scale ------------------------------------------------------


def test_indexing_is_idempotent() -> None:
    index_domain(TEST_DB, DOMAIN)
    before = _graph_snapshot()
    second = index_domain(TEST_DB, DOMAIN)
    assert _graph_snapshot() == before, "a second pass must not add nodes, edges or aliases"
    assert second.capabilities > 0


def test_index_uses_a_bounded_number_of_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    """A constant number of connections, independent of the plugin count.

    ``GraphService.upsert_node`` opens one connection per call; reusing it here
    would mean 107 connections today and 100 000 at the target scale.  This test
    is the contract that keeps the indexer batched.
    """
    import packages.graph.plugin_indexer as indexer

    calls: list[str] = []
    real_connect = indexer._connect  # type: ignore[attr-defined]

    def counting_connect(url: str) -> object:
        calls.append(url)
        return real_connect(url)

    monkeypatch.setattr(indexer, "_connect", counting_connect)
    index_domain(TEST_DB, DOMAIN)
    assert 0 < len(calls) <= 6, f"expected a constant number of connections, got {len(calls)}"


def test_indexing_does_not_expire_hand_authored_dependencies() -> None:
    """Indexing is add-only for ``depends_on``: it must not retire what it did not derive.

    The M10 migration records semantic dependencies between the shipped
    blueprints that no port contract implies (``audit.ledger.validate`` ->
    ``audit.finding.draft`` is a governance statement, not port compatibility).
    A first cut of this indexer expired every edge outside its own derivation
    and silently removed them — the planner then could not expand from the
    ledger capability at all.  ``contains`` is genuinely owned by the indexer;
    ``depends_on`` is not.
    """
    index_domain(TEST_DB, DOMAIN)
    depends = _internal_edges("depends_on")
    assert (
        "capability:audit.ledger.validate",
        "capability:audit.finding.draft",
    ) in depends


def test_legacy_blueprints_are_linked_to_their_graph_nodes() -> None:
    """The three shipped blueprints must still resolve to their graph nodes."""
    index_domain(TEST_DB, DOMAIN)
    links = _blueprint_links()
    node_keys = {node_key for _blueprint_key, node_key in links}
    assert "capability:audit.ledger.validate" in node_keys
