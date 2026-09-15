from __future__ import annotations

import os
from uuid import uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.graph.service import GraphService

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


def test_graph_route_api_is_policy_gated_and_reports_limited_bridge_route() -> None:
    service = GraphService(DB)
    suffix = uuid4().hex[:10]
    source_space = f"api-capability-{suffix}"
    target_space = f"api-domain-{suffix}"
    service.ensure_space(source_space, "L2", "API 能力图")
    service.ensure_space(target_space, "L3", "API 业务图")
    source = service.upsert_node(source_space, f"api-source:{suffix}", "capability", "受控能力")
    target = service.upsert_node(target_space, f"api-target:{suffix}", "control", "业务控制")
    service.register_bridge(source_space, target_space, source, target, "capability_contract", 0.8)

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                tenant_id,
                f"route-api-{suffix}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["graph.route.read", "graph.governance.read"]}}]),
            ),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    response = client.get(
        "/api/v1/graph/routes",
        params={
            "space_key": source_space, "start_node_id": str(source), "max_graphs": 2,
            "max_hops": 1, "max_frontier": 8, "max_nodes": 8, "max_edges": 8,
            "max_bridge_hops": 1, "max_latency_ms": 2000, "min_confidence": 0.5,
        },
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["visited_space_keys"] == [source_space, target_space]
    assert payload["edges"][-1]["kind"] == "bridge"
    assert payload["budget"]["max_bridge_hops"] == 1
    governance = client.get("/api/v1/graph/governance", headers=headers)
    assert governance.status_code == 200
    assert governance.json()["active_bridge_count"] >= 1
