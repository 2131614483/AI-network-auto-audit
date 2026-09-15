from __future__ import annotations

import os
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            row = cur.fetchone()
            assert row is not None
            return row[0]


def _client() -> tuple[TestClient, UUID]:
    tenant_id = _tenant_id()
    return TestClient(create_app(Settings(database_url=DB))), tenant_id


def test_policy_api_persists_and_replays_idempotent_decision() -> None:
    client, tenant_id = _client()
    policy_name = f"api-test-{uuid4().hex}"
    rule_id = str(uuid4())
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
                (tenant_id, policy_name, Json([{"rule_id": rule_id, "effect": "allow", "match": {"capabilities": ["audit.api.read"]}}])),
            )
    headers = {
        "X-Tenant-Id": str(tenant_id),
        "Idempotency-Key": f"policy-{uuid4().hex}",
        "X-Trace-Id": str(uuid4()),
    }
    body = {"capability": "audit.api.read", "risk_class": "read_only", "arguments": {"page": 1}}
    first = client.post("/api/v1/policy/evaluate", json=body, headers=headers)
    replay = client.post("/api/v1/policy/evaluate", json=body, headers=headers)
    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["decision"] == "ALLOW"
    assert replay.json()["decision_id"] == first.json()["decision_id"]


def test_policy_gateway_persists_an_allowed_graph_write() -> None:
    """A route-level allow is an execution decision, never an unlogged simulation."""
    client, tenant_id = _client()
    policy_name = f"route-audit-{uuid4().hex}"
    rule_id = str(uuid4())
    trace_id = str(uuid4())
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
                (
                    tenant_id,
                    policy_name,
                    Json(
                        [
                            {
                                "rule_id": rule_id,
                                "effect": "allow",
                                "match": {"capabilities": ["graph.space.write"]},
                            }
                        ]
                    ),
                ),
            )
    response = client.post(
        "/api/v1/graph/spaces",
        json={"key": f"route-audit-{uuid4().hex}", "level": "L1", "name": "策略审计回归"},
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": trace_id,
            "Idempotency-Key": f"route-audit-{uuid4().hex}",
        },
    )
    assert response.status_code == 200
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='graph.space.write'
                """,
                (trace_id,),
            )
            row = cur.fetchone()
    assert row == ("ALLOW",)


def test_policy_api_creates_and_approves_durable_approval() -> None:
    client, tenant_id = _client()
    actor_id = uuid4()
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "INSERT INTO iam.principals(id,tenant_id,kind,subject,display_name) VALUES(%s,%s,'human',%s,'API integration reviewer') "
                "ON CONFLICT (tenant_id,subject) DO UPDATE SET status='active' RETURNING id",
                (actor_id, tenant_id, f"api-test-{actor_id}"),
            )
    headers = {"X-Tenant-Id": str(tenant_id), "Idempotency-Key": f"policy-{uuid4().hex}"}
    response = client.post(
        "/api/v1/policy/evaluate",
        json={"capability": f"unlisted.{uuid4().hex}", "risk_class": "high"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "REQUIRE_APPROVAL"
    approvals = client.get("/api/v1/approvals", headers={"X-Tenant-Id": str(tenant_id)})
    assert approvals.status_code == 200
    approval = next(item for item in approvals.json()["items"] if item["tool_call_id"] == response.json()["tool_call_id"])
    decision_headers = {
            "X-Tenant-Id": str(tenant_id),
            "X-Actor-Id": str(actor_id),
        "Idempotency-Key": f"approval-{uuid4().hex}",
    }
    decided = client.post(
        f"/api/v1/approvals/{approval['id']}/decide",
        json={"decision": "approved", "reason": "integration test"},
        headers=decision_headers,
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"
    assert decided.json()["authorization_lease_id"]
    replay = client.post(
        f"/api/v1/approvals/{approval['id']}/decide",
        json={"decision": "approved", "reason": "integration test"},
        headers=decision_headers,
    )
    assert replay.status_code == 200
    assert replay.json()["authorization_lease_id"] == decided.json()["authorization_lease_id"]


def test_gateway_replay_unaffected_by_business_201_projection() -> None:
    """Replaying a key whose row was completed by the business layer (status 201)

    must neither crash with a type confusion (the 201 body is a business
    response, not a PolicyDecisionResponse) nor overwrite the recorded
    business projection.

    The ALLOW rule this test needs is upserted under a *fixed* name and set back
    to ``inactive`` in ``finally``.  The earlier version used a fresh unique name
    per run and left it ``active``, so every run permanently added another
    always-ALLOWing policy set — 1600 of them had accumulated in the shared test
    database.  That both grew without bound and made unrelated fail-closed tests
    pass for the wrong reason: they deactivated one set while a leaked one still
    granted ALLOW.  A fixed name cannot accumulate, and ``inactive`` keeps no
    ambient grant between runs.  (The app role holds no DELETE on
    ``policy.policy_sets``, so deactivating is the available cleanup.)
    """
    client, tenant_id = _client()
    policy_name = "api-replay-fixture"
    rule_id = str(uuid4())
    idempotency_key = f"policy-replay-{uuid4().hex}"

    def _set_status(status: str) -> None:
        with psycopg2.connect(DB) as connection:
            with connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
                cur.execute(
                    "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) "
                    "VALUES(%s,%s,1,%s,%s) "
                    "ON CONFLICT (tenant_id,name,version) DO UPDATE "
                    "SET status=EXCLUDED.status, rules=EXCLUDED.rules",
                    (
                        tenant_id,
                        policy_name,
                        status,
                        Json([{"rule_id": rule_id, "effect": "allow", "match": {"capabilities": ["audit.api.read"]}}]),
                    ),
                )

    _set_status("active")
    try:
        headers = {
            "X-Tenant-Id": str(tenant_id),
            "Idempotency-Key": idempotency_key,
            "X-Trace-Id": str(uuid4()),
        }
        body = {"capability": "audit.api.read", "risk_class": "read_only", "arguments": {"page": 1}}
        first = client.post("/api/v1/policy/evaluate", json=body, headers=headers)
        assert first.status_code == 200
        assert first.json()["decision"] == "ALLOW"
        # Simulate the business layer (e.g. the M9 topology service) having
        # completed the same request under the shared key: the row now holds a 201
        # projection whose body is a business response, NOT a policy decision.
        with psycopg2.connect(DB) as connection:
            with connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
                business_body: dict[str, object] = {
                    "entries": [{"seq": 1, "source_table": "topology.routing_plans", "source_pk": "abc"}],
                    "scope": "full",
                    "idempotent": False,
                }
                cur.execute(
                    "UPDATE control.idempotency_records SET response_status=201,response_json=%s "
                    "WHERE tenant_id=%s AND idempotency_key=%s RETURNING id",
                    (Json(business_body), tenant_id, idempotency_key),
                )
                projection = cur.fetchone()
        assert projection is not None, "business 201 projection must exist before replay"
        replay = client.post("/api/v1/policy/evaluate", json=body, headers=headers)
        assert replay.status_code == 200
        assert replay.json()["decision"] == "ALLOW"
        with psycopg2.connect(DB) as connection:
            with connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
                cur.execute(
                    "SELECT response_status,response_json FROM control.idempotency_records "
                    "WHERE tenant_id=%s AND idempotency_key=%s",
                    (tenant_id, idempotency_key),
                )
                row = cur.fetchone()
        assert row is not None
        assert row[0] == 201, "gateway replay must not clobber the business 201 projection"
        assert row[1]["scope"] == "full"
    finally:
        # No ambient ALLOW may survive this run.
        _set_status("inactive")
