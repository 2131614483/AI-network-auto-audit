from __future__ import annotations

import os
from uuid import uuid4

import psycopg2
import pytest

from packages.graph.service import GraphBudget, GraphService

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


def test_registered_bridge_is_the_only_cross_graph_route_and_budget_is_visible() -> None:
    service = GraphService(DB)
    suffix = uuid4().hex[:10]
    capability_space = f"capability-{suffix}"
    domain_space = f"domain-{suffix}"
    service.ensure_space(capability_space, "L2", "能力契约", cluster_key="plugins")
    service.ensure_space(domain_space, "L3", "审计业务域", cluster_key="audit")
    capability = service.upsert_node(capability_space, f"capability:{suffix}", "capability", "证据摄入契约")
    control = service.upsert_node(domain_space, f"control:{suffix}", "control", "收入截止控制")

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute("SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s", (tenant_id, capability_space))
        capability_space_id = cur.fetchone()[0]
        cur.execute("SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s", (tenant_id, domain_space))
        domain_space_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO graph.bridge_edges(
            tenant_id,source_space_id,target_space_id,source_node_id,target_node_id,relation_type,weight)
            VALUES(%s,%s,%s,%s,%s,'capability_contract',0.9)""",
            (tenant_id, capability_space_id, domain_space_id, capability, control),
        )
        with pytest.raises(psycopg2.Error, match="registered bridge rule"):
            connection.commit()
        connection.rollback()

    bridge_id = service.register_bridge(
        capability_space, domain_space, capability, control, "capability_contract", 0.9
    )
    assert bridge_id
    routed = service.bounded_route(
        capability_space,
        capability,
        GraphBudget(max_graphs=2, max_hops=1, max_frontier=8, max_nodes=8, max_edges=8,
                    max_bridge_hops=1, max_latency_ms=2_000, min_confidence=0.5),
    )
    assert {str(capability), str(control)}.issubset(set(routed["nodes"]))
    assert routed["visited_space_keys"] == [capability_space, domain_space]
    assert routed["edges"][-1]["kind"] == "bridge"
    assert routed["edges"][-1]["relation"] == "capability_contract"
    assert routed["budget"]["max_graphs"] == 2

    constrained = service.bounded_route(
        capability_space,
        capability,
        GraphBudget(max_graphs=1, max_hops=1, max_frontier=8, max_nodes=8, max_edges=8,
                    max_bridge_hops=1, max_latency_ms=2_000, min_confidence=0),
    )
    assert "max_graphs" in constrained["truncation_reasons"]
    assert constrained["visited_space_keys"] == [capability_space]


def test_node_versions_are_immutable_and_rollback_preserves_history() -> None:
    service = GraphService(DB)
    suffix = uuid4().hex[:10]
    space = f"revision-{suffix}"
    service.ensure_space(space, "L3", "版本治理")
    node = service.upsert_node(space, f"node:{suffix}", "entity", "初始标签", {"version": 1})
    service.upsert_node(space, f"node:{suffix}", "entity", "更新标签", {"version": 2})
    revisions = service.list_node_revisions(node)
    assert revisions and revisions[0]["snapshot"]["label"] == "初始标签"
    service.rollback_node_revision(node, revisions[0]["id"])
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute("SELECT label,properties,row_version FROM graph.nodes WHERE tenant_id=%s AND id=%s", (tenant_id, node))
        label, properties, version = cur.fetchone()
    assert label == "初始标签"
    assert properties == {"version": 1}
    assert version >= 3
    assert any(revision["event_type"] == "rollback" for revision in service.list_node_revisions(node))
