from __future__ import annotations

import os
from uuid import uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def _allow_dashboard_read(connection) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                tenant_id,
                f"operations-detail-{uuid4().hex}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["platform.dashboard.read"]}}]),
            ),
        )
        return tenant_id


def test_operations_detail_returns_hierarchical_run_projection() -> None:
    """GET /api/v1/ui/operations/detail 返回 Mission→Workflow→Task→Agent 层级运行投影。"""
    with psycopg2.connect(DB) as connection:
        tenant_id = _allow_dashboard_read(connection)

    response = TestClient(create_app(Settings(database_url=DB))).get(
        "/api/v1/ui/operations/detail",
        headers={"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "trace_id" in payload
    missions = payload["missions"]
    assert isinstance(missions, list)

    # 层级字段存在性：任取一条 mission 校验四层结构与核心字段
    for mission in missions:
        assert set(mission) >= {"id", "title", "domain", "autonomy_mode", "status", "created_at", "workflows"}
        for workflow in mission["workflows"]:
            assert set(workflow) >= {"id", "workflow_key", "workflow_version", "status", "trace_id", "tasks"}
            for task in workflow["tasks"]:
                assert set(task) >= {
                    "id", "node_key", "capability", "status", "attempt", "max_attempts",
                    "started_at", "finished_at", "error_detail", "trace_id", "agents",
                }
                for agent in task["agents"]:
                    assert set(agent) >= {
                        "id", "role_key", "model_key", "status",
                        "token_input", "token_output", "cost", "started_at", "finished_at", "error_detail",
                    }
        # 至少验证一条 mission 的 workflows 字段类型正确
        assert isinstance(mission["workflows"], list)
        break


def test_operations_detail_respects_policy_gate() -> None:
    """无策略集的新租户访问详情端点必须 fail-closed（403/409）。"""
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        slug = f"ops-detail-denied-{uuid4().hex[:8]}"
        cur.execute("INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING", (slug, "detail denied"))
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (slug,))
        tenant_id = cur.fetchone()[0]

    response = TestClient(create_app(Settings(database_url=DB))).get(
        "/api/v1/ui/operations/detail",
        headers={"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())},
    )
    assert response.status_code in (403, 409)
