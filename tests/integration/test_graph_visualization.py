from __future__ import annotations

import os
from uuid import uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.graph.service import GraphService

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def test_graph_visualization_api_returns_bounded_typed_topology() -> None:
    service = GraphService(DB)
    suffix = uuid4().hex[:10]
    space_key = f"visual-{suffix}"
    service.ensure_space(space_key, "L2", "可视化验证图")
    control = service.upsert_node(space_key, f"control:{suffix}", "control", "收入截止控制")
    evidence = service.upsert_node(space_key, f"evidence:{suffix}", "evidence", "期后发票")
    service.upsert_edge(space_key, control, evidence, "supported_by", 0.9)

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                tenant_id,
                f"graph-visual-{suffix}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["graph.visualize.read"]}}]),
            ),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    response = client.get(
        "/api/v1/graph/visualization",
        params={"space_key": space_key, "max_nodes": 20, "max_edges": 20},
        headers={"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["space_key"] == space_key
    assert len(body["nodes"]) == 2
    assert body["nodes"][0]["node_type"] in {"control", "evidence"}
    # 边除权重外必须带**依据**：这两端是测试造的合成节点，既无端口衔接也无运行实测，
    # 因此权重退回库里存的值、依据如实说明"没有证据"—— 不允许只说数字不说来源。
    assert len(body["edges"]) == 1
    edge = body["edges"][0]
    assert {key: edge[key] for key in ("source", "target", "relation", "weight")} == {
        "source": str(control), "target": str(evidence), "relation": "supported_by", "weight": 0.9,
    }
    assert edge["basis"], "每条边都要说明权重依据"
    assert body["partial"] is False
    assert any(space["key"] == space_key for space in body["spaces"])
