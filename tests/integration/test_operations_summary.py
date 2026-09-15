from __future__ import annotations

import os
from uuid import uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def test_operations_summary_reports_every_workbench_from_real_database() -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                tenant_id,
                f"operations-api-{uuid4().hex}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["platform.dashboard.read"]}}]),
            ),
        )
    response = TestClient(create_app(Settings(database_url=DB))).get(
        "/api/v1/ui/operations",
        headers={"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())},
    )
    assert response.status_code == 200
    counts = response.json()["counts"]
    assert {
        "missions", "workflow_runs", "task_runs", "agent_runs", "plugins", "graph_spaces",
        "graph_nodes", "graph_edges", "audit_engagements", "audit_findings", "quant_backtests",
        "aiops_incidents", "aiops_executions",
    }.issubset(counts)
    assert isinstance(response.json()["recent_decisions"], list)
