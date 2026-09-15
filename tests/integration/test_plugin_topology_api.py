"""Plugin Topology M1 read-only API surface against the isolated test database.

Proves fail-closed policy gating, tenant context, and the four read-only
endpoints (clusters / blueprints / releases / plans) declared for the
desktop IPC whitelist.  Nothing here executes a plugin.
"""

from __future__ import annotations

import hashlib
import os
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _allow(connection, tenant_id: UUID, name: str, capability: str, risk_class: str) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                tenant_id,
                name,
                psycopg2.extras.Json([{
                    "rule_id": str(uuid4()), "effect": "allow",
                    "match": {
                        "capabilities": [capability], "risk_classes": [risk_class],
                        "side_effects": ["read_only" if risk_class == "read_only" else "write_data"],
                    },
                }]),
            ),
        )


def _allow_all() -> PolicyEngine:
    return PolicyEngine(allow=[
        "topology.cluster.write", "topology.blueprint.write", "topology.membership.write",
        "topology.edge.write", "topology.contract.write", "topology.release.publish",
        "topology.plan.read",
    ])


def _cluster(marker: str) -> dict:
    return {
        "kind": "cluster",
        "idempotency_key": f"{marker}-cluster",
        "payload": {
            "key": f"api-{marker}-domain",
            "name": f"API 测试域-{marker}",
            "axis": "business_domain",
            "layer": "L0",
            "domains": ["audit"],
            "routing_budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            "allowed_bridge_kinds": ["artifact_ref", "capability_contract"],
        },
    }


def test_topology_read_api_is_policy_gated_and_returns_tenant_rows() -> None:
    marker = uuid4().hex[:8]
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%topology.%"),
        )

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    # fail closed without an active allow rule
    assert client.get("/api/v1/topology/clusters", headers=headers).status_code in (403, 409)
    assert client.get("/api/v1/topology/blueprints", headers=headers).status_code in (403, 409)
    assert client.get("/api/v1/topology/releases", headers=headers).status_code in (403, 409)
    assert client.get("/api/v1/topology/plans", headers=headers).status_code in (403, 409)
    assert client.get("/api/v1/topology/bridges", headers=headers).status_code in (403, 409)

    # register tenant-local catalog behind the service gateway
    service = TopologyService(DB, policy=_allow_all())
    registered = service.upsert(_cluster(marker))
    assert registered["kind"] == "cluster"

    # re-allow the seeded read capabilities
    with psycopg2.connect(DB) as connection:
        _allow(connection, tenant_id, f"topo-read-{marker}-a", "topology.cluster.read", "read_only")
        _allow(connection, tenant_id, f"topo-read-{marker}-b", "topology.blueprint.read", "read_only")
        _allow(connection, tenant_id, f"topo-read-{marker}-c", "topology.plan.read", "read_only")
        _allow(connection, tenant_id, f"topo-read-{marker}-d", "topology.bridge.read", "read_only")

    clusters = client.get("/api/v1/topology/clusters", headers=headers)
    assert clusters.status_code == 200
    assert any(item["key"] == f"api-{marker}-domain" for item in clusters.json()["items"])

    blueprints = client.get("/api/v1/topology/blueprints", headers=headers)
    assert blueprints.status_code == 200
    assert isinstance(blueprints.json()["items"], list)

    releases = client.get("/api/v1/topology/releases", headers=headers)
    assert releases.status_code == 200
    assert isinstance(releases.json()["items"], list)

    plans = client.get("/api/v1/topology/plans", headers=headers)
    assert plans.status_code == 200
    assert isinstance(plans.json()["items"], list)

    # Request the full page (the API caps ``limit`` at 500): the seeded bridge
    # must be asserted by presence, not by surviving pagination.  Every topology
    # suite inserts ``it-*`` blueprints/bridges and never removes them (784 of
    # 787 blueprints, 100 of 101 bridges in the test database), and ``list_bridges``
    # orders by blueprint key then truncates — so the default page of 100 pushed
    # the alphabetically-later ``ledger-quality-slot`` off the end.
    bridges = client.get("/api/v1/topology/bridges", headers=headers, params={"limit": 500})
    assert bridges.status_code == 200
    # the migration seeds a public bridge on ledger-quality-slot
    assert any(item["blueprint_key"] == "ledger-quality-slot" for item in bridges.json()["items"])

    # unknown tenant context is rejected, never an empty silent list
    foreign = client.get(
        "/api/v1/topology/clusters",
        headers={"X-Tenant-Id": str(uuid4()), "X-Trace-Id": str(uuid4())},
    )
    assert foreign.status_code == 404


def test_topology_chains_api_is_policy_gated_and_returns_seeded_rows() -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT chain_key FROM topology.invocation_chains "
            "WHERE tenant_id=%s AND chain_key=%s",
            (tenant_id, SEED_CHAIN_KEY),
        )
        seeded_row = cur.fetchone()
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%topology.%"),
        )

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    # fail closed: chain/intent reads and chain materialization need allow rules
    assert client.get("/api/v1/topology/chains", headers=headers).status_code in (403, 409)
    assert client.get("/api/v1/topology/chains/not-a-chain/intents", headers=headers).status_code in (403, 409)
    assert client.post(
        "/api/v1/topology/chains/materialize",
        headers={**headers, "Idempotency-Key": "m3-api-denied"},
        json={"plan_key": "plan-" + "a" * 16, "idempotency_key": "m3-api-denied"},
    ).status_code in (403, 409)

    with psycopg2.connect(DB) as connection:
        _allow(connection, tenant_id, f"m3-chain-read-{uuid4().hex[:6]}", "topology.chain.read", "read_only")
        _allow(connection, tenant_id, f"m3-intent-read-{uuid4().hex[:6]}", "topology.intent.read", "read_only")

    chains = client.get("/api/v1/topology/chains", headers=headers)
    assert chains.status_code == 200
    assert isinstance(chains.json()["items"], list)
    # 迁移种子链（0040 物化）是只读 API 数据基座的一部分；账本/意图读取按
    # 链 key 走 DB 验证（列表接口有分页，不依赖种子链出现在最新 N 条）。
    assert seeded_row is not None, "0040 迁移种子链必须存在于测试库"
    intents = client.get(f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/intents", headers=headers)
    assert intents.status_code == 200
    items = intents.json()["items"]
    assert [item.get("slot_key") for item in items] == ["audit.ledger.validate", "quant.research-note.draft"]
    # every projection carries the four-state status and a valid slot identity
    for item in items:
        assert item.get("slot_key")
        assert item.get("status") in {"materialized", "policy_allowed", "approved_projection", "denied"}
        assert item.get("policy_decision") in {"allowed", "requires_approval", "denied"}

    # unknown plan_key is a 400, never a silent success
    with psycopg2.connect(DB) as connection:
        _allow(connection, tenant_id, f"m3-chain-write-{uuid4().hex[:6]}", "topology.chain.write", "low")
    bad_materialize = client.post(
        "/api/v1/topology/chains/materialize",
        headers={**headers, "Idempotency-Key": "m3-api-ghost"},
        json={"plan_key": "plan-" + "b" * 16, "idempotency_key": "m3-api-ghost"},
    )
    assert bad_materialize.status_code == 400
    assert "routing plan not found" in bad_materialize.json()["detail"]


# -- M4: approvals & shadow execution ledger ------------------------------------

SEED_CHAIN_KEY = "chain-" + hashlib.sha256(
    "|".join([
        "plan-" + hashlib.sha256(b"ledger-quality-slot|research-note-slot").hexdigest()[:16],
        "ledger-quality-slot",
        "research-note-slot",
    ]).encode("utf-8")
).hexdigest()[:16]


def _deactivate_topology_policies(connection, tenant_id: UUID) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%topology.%"),
        )


def test_topology_chain_m4_endpoints_fail_closed_without_policy() -> None:
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _deactivate_topology_policies(connection, tenant_id)

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    # approval write, shadow-execution write and both ledger reads fail closed
    assert client.post(
        f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/approvals",
        headers=headers,
        json={"chain_key": SEED_CHAIN_KEY, "slot_key": "quant.research-note.draft", "decision": "approve",
              "idempotency_key": "m4-api-denied"},
    ).status_code in (403, 409)
    assert client.post(
        f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/executions",
        headers=headers,
        json={"chain_key": SEED_CHAIN_KEY, "mode": "simulated", "idempotency_key": "m4-api-denied"},
    ).status_code in (403, 409)
    assert client.get(f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/approvals", headers=headers).status_code in (403, 409)
    assert client.get(f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/executions", headers=headers).status_code in (403, 409)


def test_topology_chain_execute_api_returns_seeded_already_run() -> None:
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _deactivate_topology_policies(connection, tenant_id)
        _allow(connection, tenant_id, f"m4-exec-{uuid4().hex[:6]}", "topology.chain.execute", "medium")
        _allow(connection, tenant_id, f"m4-lgr-{uuid4().hex[:6]}", "topology.execution.read", "read_only")
        _allow(connection, tenant_id, f"m4-rd-{uuid4().hex[:6]}", "topology.chain.read", "read_only")

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    executed = client.post(
        f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/executions",
        headers=headers,
        json={"chain_key": SEED_CHAIN_KEY, "mode": "simulated", "idempotency_key": f"m4-api-exec-{uuid4().hex[:16]}"},
    )
    assert executed.status_code == 200
    body = executed.json()
    assert body["mode"] == "simulated"
    assert body["status"] == "already_executed"
    assert len(body["chain_checksum"]) == 64

    # explicit large limit: the persistent test database accumulates isolated
    # ledger rows on the seeded chain across M4-M10 drills, which would
    # otherwise push the oldest (simulated) seed rows past the default; under
    # started_at DESC ordering the simulated seeds sort LAST.  Raised to
    # 10000 after R4 observed 1148 accumulated rows on the seeded chain.
    ledger = client.get(f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/executions?limit=10000", headers=headers)
    assert ledger.status_code == 200
    items = ledger.json()["items"]
    simulated_items = [item for item in items if item["mode"] == "simulated"]
    assert [item["slot_key"] for item in simulated_items] == ["audit.ledger.validate", "quant.research-note.draft"]
    assert all(item["mode"] == "simulated" and len(item["output_checksum"]) == 64 for item in simulated_items)

    approvals = client.get(f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/approvals", headers=headers)
    assert approvals.status_code == 200
    assert any(
        item["slot_key"] == "quant.research-note.draft" and item["decision"] == "approve"
        for item in approvals.json()["items"]
    )


def test_topology_chain_approval_api_rejects_finalized_seed_intent() -> None:
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _deactivate_topology_policies(connection, tenant_id)
        _allow(connection, tenant_id, f"m4-apr-{uuid4().hex[:6]}", "topology.chain.approve", "low")

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    # the seeded research intent is already policy_allowed, so the state
    # machine must refuse to touch it instead of silently approving
    response = client.post(
        f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/approvals",
        headers=headers,
        json={"chain_key": SEED_CHAIN_KEY, "slot_key": "quant.research-note.draft", "decision": "approve",
              "idempotency_key": "m4-api-apr-final", "reason": "不应可审批"},
    )
    assert response.status_code == 400
    assert "not awaiting approval" in response.json()["detail"]


def test_topology_chain_execute_api_contract_rejects_isolated_mode_and_missing_key() -> None:
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _deactivate_topology_policies(connection, tenant_id)
        _allow(connection, tenant_id, f"m4-exec2-{uuid4().hex[:6]}", "topology.chain.execute", "medium")

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    # M5: isolated 必须说明演练用途 reason，空 reason 被应用层拒绝（契约级校验）
    isolated = client.post(
        f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/executions",
        headers=headers,
        json={"chain_key": SEED_CHAIN_KEY, "mode": "isolated", "idempotency_key": "m4-api-isolated"},
    )
    assert isolated.status_code == 400
    assert "requires a reason" in isolated.json()["detail"]
    # M5: 未授予 execute.isolated 双策略权限 → 403 fail-closed（零子进程）
    no_twin = client.post(
        f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/executions",
        headers=headers,
        json={"chain_key": SEED_CHAIN_KEY, "mode": "isolated", "idempotency_key": f"m4-api-twin-{uuid4().hex[:8]}",
              "reason": "受限演练意图书面声明"},
    )
    assert no_twin.status_code in (403, 409)
    # every write must carry an Idempotency-Key in the body
    missing_key = client.post(
        f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/executions",
        headers=headers,
        json={"chain_key": SEED_CHAIN_KEY, "mode": "simulated"},
    )
    assert missing_key.status_code == 422


def test_topology_chain_m4_reads_are_rls_isolated_per_tenant() -> None:
    marker = uuid4().hex[:8]
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _tenant(connection)
        cur.execute("INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING",
                    (f"m4-api-{marker}", "M4 API 其他租户"))
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (f"m4-api-{marker}",))
        other_id = cur.fetchone()[0]

    with psycopg2.connect(DB) as connection:
        # the other tenant can read, but RLS must show zero of local-dev's rows
        _allow(connection, other_id, f"m4-oth-{marker}", "topology.execution.read", "read_only")

    client = TestClient(create_app(Settings(database_url=DB)))
    other_headers = {"X-Tenant-Id": str(other_id), "X-Trace-Id": str(uuid4())}
    ledger = client.get(f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/executions", headers=other_headers)
    assert ledger.status_code == 200
    assert ledger.json()["items"] == []
    approvals = client.get(f"/api/v1/topology/chains/{SEED_CHAIN_KEY}/approvals", headers=other_headers)
    assert approvals.status_code == 200
    assert approvals.json()["items"] == []
