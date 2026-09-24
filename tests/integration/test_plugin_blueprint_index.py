"""插件目录 → 组网蓝图目录的注册契约（连通"图谱召回"与"真正组网"）。

背景：图谱能召回插件（S1/S3）并不等于组网能用它。``GraphPlanningService``
把节点翻译成组网需求的路径是 ``blueprint_graph_links`` → ``plugin_blueprints``，
而这两张表原先只有 3 个 legacy 槽位 —— 所以召回出的 107 个真实插件
``capability_requirements`` 恒为空，``plan_from_intent`` **fail-closed**。

本文件锁定"插件也进入蓝图目录"，以及由此打通的端到端链路：

  - 每个插件成为 ``plugin_blueprints`` 的一行（``planned``、``capability``）；
  - **每个 blueprint 至少属于一个 cluster** —— 这不是可选项，
    ``TopologyPlanner.plan`` 对没有 memberships 的候选直接跳过；
  - ``capability_contract`` 的端口与插件协议一致；
  - blueprint 之间的 ``topology_edges`` 由端口 contract 兼容性推导；
  - 幂等；且真实插件的意图能真的产出 plan_only 计划。
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID, uuid4

import psycopg2

from packages.graph.graph_planning import CapabilityGraphAdapter
from packages.plugin_topology.blueprint_indexer import BlueprintIndexResult, index_blueprints
from packages.plugin_topology.graph_planning import GraphPlanningService
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

TEST_DB = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or (
    "postgresql://audit_app:admin@localhost:5432/audit_network_test"
)
DOMAIN = "audit"
_GRAPH_PLAN_CAPABILITIES = [
    "topology.intent.plan",
    "topology.plan.read",
    "topology.chain.write",
    "topology.chain.approve",
    "topology.chain.execute",
    "topology.chain.execute.isolated",
]
# A real plugin from the on-disk directory, reached by its own Chinese name.
_REAL_PLUGIN_ID = "audit.evidence.workpaper-review3"
_REAL_CAPABILITY = "audit.evidence.workpaper-review3"


def _tid(cur: Any) -> UUID:
    """Scope *this* cursor's connection to the tenant and return the tenant id.

    ``topology.*`` is under RLS too, so the session variable has to be set on the
    same connection that runs the query.  Opening a second connection just to
    look the tenant up would leave this one unscoped, and every read would come
    back empty — which looks exactly like "the indexer wrote nothing".
    """
    cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
    row = cur.fetchone()
    assert row is not None
    tenant_id = UUID(str(row[0]))
    cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
    cur.fetchone()
    return tenant_id


def _specs() -> dict[str, Any]:
    """The plugins the catalog registers: ``verified`` only.

    The blueprint catalog is what the planner selects *and then executes*, so
    ``contract_only`` plugins stay out of it — they declare an interface but ship
    no runnable implementation.  The graph index keeps the full directory.
    """
    from packages.ai_planner.workbench import _specs_for

    return {pid: spec for pid, spec in _specs_for(DOMAIN).items() if spec.lifecycle == "verified"}


def _blueprint(key: str) -> dict[str, Any] | None:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        tenant_id = _tid(cur)
        cur.execute(
            "SELECT status, blueprint_type, capability_contract FROM topology.plugin_blueprints "
            "WHERE tenant_id=%s AND key=%s",
            (tenant_id, key),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {"status": row[0], "blueprint_type": row[1], "contract": row[2]}


def _memberships(blueprint_key: str) -> list[str]:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            """SELECT c.key FROM topology.cluster_memberships m
               JOIN topology.plugin_blueprints b ON b.id = m.blueprint_id
               JOIN topology.plugin_clusters c ON c.id = m.cluster_id
               WHERE m.tenant_id=%s AND b.key=%s""",
            (_tid(cur), blueprint_key),
        )
        return [str(row[0]) for row in cur.fetchall()]


def _topology_edges() -> set[tuple[str, str]]:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            """SELECT s.key, t.key FROM topology.topology_edges e
               JOIN topology.plugin_blueprints s ON s.id = e.source_blueprint_id
               JOIN topology.plugin_blueprints t ON t.id = e.target_blueprint_id
               WHERE e.tenant_id=%s AND e.status='active' AND e.relation_type='depends_on'""",
            (_tid(cur),),
        )
        return {(str(a), str(b)) for a, b in cur.fetchall()}


def _blueprint_count() -> int:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM topology.plugin_blueprints WHERE tenant_id=%s", (_tid(cur),)
        )
        return int(cur.fetchone()[0])


def _graph_plan_service() -> GraphPlanningService:
    return GraphPlanningService(
        TEST_DB,
        topology_service=TopologyService(TEST_DB, policy=PolicyEngine(allow=_GRAPH_PLAN_CAPABILITIES)),
        graph_port=CapabilityGraphAdapter(TEST_DB),
        policy=PolicyEngine(allow=_GRAPH_PLAN_CAPABILITIES),
    )


# -- 1. plugins become blueprints, and every blueprint is reachable --------------


def test_every_plugin_becomes_a_planned_capability_blueprint() -> None:
    result = index_blueprints(TEST_DB, DOMAIN)
    assert isinstance(result, BlueprintIndexResult)
    specs = _specs()
    for plugin_id in specs:
        record = _blueprint(plugin_id)
        assert record is not None, f"{plugin_id} has no blueprint"
        assert record["status"] == "planned"  # _load_catalog only reads planned/released
        assert record["blueprint_type"] == "capability"
    assert result.blueprints >= len(specs)


def test_every_blueprint_belongs_to_a_cluster() -> None:
    """Not decorative: the planner skips any candidate without memberships."""
    index_blueprints(TEST_DB, DOMAIN)
    for plugin_id in list(_specs())[:20]:
        assert _memberships(plugin_id), f"{plugin_id} belongs to no cluster"


def test_capability_contract_mirrors_the_plugin_ports() -> None:
    index_blueprints(TEST_DB, DOMAIN)
    spec = _specs()[_REAL_PLUGIN_ID]
    contract = _blueprint(_REAL_PLUGIN_ID)["contract"]  # type: ignore[index]
    assert contract["capability"] == spec.capability
    assert contract["inputs"] == [port.port_id for port in spec.inputs]
    assert contract["outputs"] == [port.port_id for port in spec.outputs]


def test_topology_edges_mirror_contract_compatibility() -> None:
    from packages.ai_planner.connectivity import contract_edges

    index_blueprints(TEST_DB, DOMAIN)
    specs = _specs()
    expected = contract_edges(specs)
    assert expected, "audit plugins must have contract-compatible pairs"
    actual = _topology_edges()
    missing = expected - actual
    assert missing == set(), f"compatible pairs missing from topology_edges: {sorted(missing)[:5]}"


def test_blueprint_graph_links_join_the_two_registries() -> None:
    """The link is what lets a recalled graph node resolve to a blueprint."""
    index_blueprints(TEST_DB, DOMAIN)
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT blueprint_key, node_key FROM topology.blueprint_graph_links "
            "WHERE tenant_id=%s AND blueprint_key=%s",
            (_tid(cur), _REAL_PLUGIN_ID),
        )
        rows = cur.fetchall()
    assert (str(rows[0][0]), str(rows[0][1])) == (
        _REAL_PLUGIN_ID, f"capability:{_REAL_CAPABILITY}"
    )


def test_blueprint_indexing_is_idempotent() -> None:
    index_blueprints(TEST_DB, DOMAIN)
    before = (_blueprint_count(), len(_topology_edges()))
    index_blueprints(TEST_DB, DOMAIN)
    assert (_blueprint_count(), len(_topology_edges())) == before


# -- 2. the end-to-end payoff -----------------------------------------------------


def test_a_real_plugin_intent_plans_instead_of_failing_closed() -> None:
    """The whole point: a real plugin reaches a plan_only plan.

    Before this registration the intent below failed closed — the graph matched
    the plugin, but nothing linked it to a blueprint, so
    ``capability_requirements`` came out empty.
    """
    index_blueprints(TEST_DB, DOMAIN)
    result = _graph_plan_service().plan_from_intent(
        {
            "intent": "底稿三级复核",
            "budget": {"max_matches": 4, "expand_hops": 0},
            "idempotency_key": f"blueprint-intent-{uuid4().hex[:8]}",
            "reason": "验证真实插件可进入组网计划",
        }
    )
    assert result["mode"] == "plan_only"
    assert _REAL_CAPABILITY in result["capability_requirements"]
    assert result["plan_key"].startswith("plan-")
    assert len(result["plan_checksum"]) == 64
