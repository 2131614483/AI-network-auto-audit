"""CW0-A contract tests: observability endpoints must be tenant-scoped,
policy-gated, and must never disguise a database failure as an empty success.

Acceptance (方案 第13节 验收矩阵 CW0 出口):
  1. trace-locate requires X-Tenant-Id (missing -> 422).
  2. trace-locate is policy-gated (no active rule -> 403/409).
  3. trace-locate returns rows only for the caller tenant (cross-tenant -> 0 rows).
  4. code-map is policy-gated and echoes the caller tenant.
  5. DB failure -> 5xx with completeness=unavailable, never 200 + empty rows.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg2
import psycopg2.extras
from fastapi.testclient import TestClient

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"


def _tenant(connection, slug: str) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (slug,))
        row = cur.fetchone()
        assert row is not None, f"tenant {slug} must exist"
        return UUID(str(row[0]))


def _allow(connection, tenant_id: UUID, name: str, capability: str) -> None:
    from uuid import uuid4 as _u4

    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                str(tenant_id),
                name,
                psycopg2.extras.Json([
                    {
                        "rule_id": str(_u4()), "effect": "allow",
                        "match": {
                            "capabilities": [capability], "risk_classes": ["read_only"],
                            "side_effects": ["read_only"],
                        },
                    }
                ]),
            ),
        )


def _create_other_tenant(connection, slug: str) -> UUID:
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING",
            (slug, "CW0 其他租户"),
        )
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (slug,))
        return UUID(str(cur.fetchone()[0]))


def _seed_trace(tenant_id: UUID, trace_id: str) -> None:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            """INSERT INTO control.workflow_runs
               (tenant_id,workflow_key,workflow_version,status,idempotency_key,trace_id)
               VALUES(%s,%s,'1.0.0','completed',%s,%s) RETURNING id""",
            (str(tenant_id), f"cw0-a-wf-{uuid4().hex[:6]}", uuid4().hex, trace_id),
        )
        workflow_run_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO control.task_runs
               (tenant_id,workflow_run_id,node_key,capability,status,trace_id,lease_token,lease_expires_at,idempotency_key)
               VALUES(%s,%s,%s,%s,'completed',%s,%s,now()+'1 hour',%s)""",
            (
                str(tenant_id), str(workflow_run_id),
                f"cw0-a-node-{uuid4().hex[:6]}", "audit.cw0.synthetic", trace_id,
                uuid4().hex, uuid4().hex,
            ),
        )


def _client(monkeypatch, database_url: str = TEST_DB) -> TestClient:
    from apps.api.main import Settings, create_app

    return TestClient(create_app(Settings(database_url=database_url)))


def test_trace_locate_requires_tenant_header(monkeypatch) -> None:
    client = _client(monkeypatch)
    # 出口1: X-Tenant-Id is mandatory -> 422 when absent.
    assert client.get(f"/api/v1/observability/trace/{uuid4()}").status_code == 422


def test_trace_locate_is_policy_gated_and_tenant_isolated(monkeypatch) -> None:
    trace_id = str(uuid4())
    with psycopg2.connect(TEST_DB) as connection:
        owner = _tenant(connection, "local-dev")
        other = _create_other_tenant(connection, f"cw0-b-{uuid4().hex[:8]}")
        _allow(connection, owner, f"cw0-trace-{uuid4().hex[:6]}", "observability.trace.read")
        _allow(connection, other, f"cw0-trace-{uuid4().hex[:6]}", "observability.trace.read")
    _seed_trace(owner, trace_id)

    client = _client(monkeypatch)
    head = {"X-Tenant-Id": str(owner), "X-Trace-Id": str(uuid4())}

    # 出口2: without any policy the read is fail-closed.
    with psycopg2.connect(TEST_DB) as connection:
        _create_other_tenant(connection, "cw0-no-policy")
    with psycopg2.connect(TEST_DB) as connection:
        no_policy = _tenant(connection, "cw0-no-policy")
    denied = client.get(
        f"/api/v1/observability/trace/{trace_id}",
        headers={"X-Tenant-Id": str(no_policy), "X-Trace-Id": str(uuid4())},
    )
    assert denied.status_code in (403, 409)

    # 出口3: owner sees its own rows and a completeness marker.
    ok = client.get(f"/api/v1/observability/trace/{trace_id}", headers=head)
    assert ok.status_code == 200
    body = ok.json()
    assert body["trace_id"] == trace_id
    assert body["completeness"] == "complete"
    assert any(row["kind"] == "task_run" for row in body["data_rows"])

    # 出口3 (cross-tenant): another tenant with the same policy sees 0 rows.
    cross = client.get(
        f"/api/v1/observability/trace/{trace_id}",
        headers={"X-Tenant-Id": str(other), "X-Trace-Id": str(uuid4())},
    )
    assert cross.status_code == 200
    assert cross.json()["data_rows"] == []
    assert cross.json()["completeness"] == "complete"


def test_code_map_is_policy_gated_and_tenant_scoped(monkeypatch) -> None:
    with psycopg2.connect(TEST_DB) as connection:
        no_policy = _create_other_tenant(connection, f"cw0-c-{uuid4().hex[:8]}")
        owner = _tenant(connection, "local-dev")
        _allow(connection, owner, f"cw0-codemap-{uuid4().hex[:6]}", "observability.code-map.read")

    client = _client(monkeypatch)
    denied = client.get(
        "/api/v1/observability/code-map",
        headers={"X-Tenant-Id": str(no_policy), "X-Trace-Id": str(uuid4())},
    )
    assert denied.status_code in (403, 409)

    ok = client.get(
        "/api/v1/observability/code-map",
        headers={"X-Tenant-Id": str(owner), "X-Trace-Id": str(uuid4())},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["tenant_id"] == str(owner)
    assert body["endpoint_count"] > 0


def test_trace_locate_db_failure_is_unavailable_not_empty_success(monkeypatch) -> None:
    # 出口4: a broken database must surface as 5xx/unavailable, never 200+[].
    bad = _client(
        monkeypatch,
        database_url="postgresql://audit_app:admin@localhost:59999/audit_network_test",
    )
    response = bad.get(
        f"/api/v1/observability/trace/{uuid4()}",
        headers={"X-Tenant-Id": "44476992-6903-4391-b45e-afb6cc0ac02b", "X-Trace-Id": str(uuid4())},
    )
    assert response.status_code == 503
    body = response.json()
    # never a fake empty success: the body must not look like a 200 locate result
    assert "data_rows" not in body
