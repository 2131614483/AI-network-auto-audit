"""Integration tests for the dynamic plugin-nebula read endpoint.

Proves the desktop 知识星云 graph is policy-gated, tenant-scoped, traceable,
and reflects the live plugin directory — without executing any plugin.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
CAP = "topology.plugin_nebula.read"
PATH = "/api/v1/topology/plugin-nebula"


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _allow(connection, tenant_id: UUID, name: str) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                tenant_id, name,
                psycopg2.extras.Json([{
                    "rule_id": str(uuid4()), "effect": "allow",
                    "match": {"capabilities": [CAP], "risk_classes": ["read_only"],
                              "side_effects": ["read_only"]},
                }]),
            ),
        )


def test_nebula_is_policy_gated_then_returns_live_graph() -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%plugin_nebula%"),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}

    # fail closed without an active allow rule
    assert client.get(PATH, headers=headers).status_code in (403, 409)

    with psycopg2.connect(DB) as connection:
        _allow(connection, tenant_id, f"nebula-read-{uuid4().hex[:8]}")

    ok = client.get(PATH, headers=headers)
    assert ok.status_code == 200
    body = ok.json()
    assert set(body) == {"stages", "nodes", "edges", "trunk", "layers", "stats", "trace_id"}
    assert UUID(body["trace_id"])
    # reflects the full live contract directory (100 verified + legacy contracts)
    assert body["stats"]["total"] >= 100
    layers = {n["layer"] for n in body["nodes"]}
    assert layers == {"biz", "gov", "base"}
    # every node carries a non-empty Chinese display name and a valid layer/stage
    for node in body["nodes"]:
        assert node["name"]
        if node["layer"] == "biz":
            assert node["stage"] and node["stage"].startswith("s")
        else:
            assert node["stage"] is None
    # eight business stages + closed trunk ring
    assert len(body["stages"]) == 8
    assert body["trunk"][0] == {"source": "s1", "target": "s2", "type": "trunk", "contract": ""}
    assert body["trunk"][-1] == {"source": "gov", "target": "s1", "type": "trunk", "contract": ""}
    # subject-to-subject data-interface edges are present and port-consistent
    flow = [e for e in body["edges"] if e["type"] == "dataflow"]
    assert len(flow) >= 60
    by_id = {n["id"]: n for n in body["nodes"]}
    for edge in flow:
        assert edge["contract"]
        assert edge["contract"] in by_id[edge["source"]]["outputs"]
        assert edge["contract"] in by_id[edge["target"]]["inputs"]
    assert body["stats"]["dataflow_edges"] == len(flow)
    # cross-layer capability calls (business/governance -> foundation), from invokes
    cross = [e for e in body["edges"] if e["type"] == "cross"]
    assert len(cross) == 34
    for edge in cross:
        assert by_id[edge["source"]]["layer"] in {"biz", "gov"}
        assert by_id[edge["target"]]["layer"] == "base"
    assert body["stats"]["cross_edges"] == len(cross)
    # version + derivation surface: every shipped plugin declares a version, and
    # none declares a base yet, so the derivation layer is present but empty.
    for node in body["nodes"]:
        assert node["version"]
        assert node["derived_from"] == ""
    assert not [e for e in body["edges"] if e["type"] == "derivation"]
    assert body["stats"]["derivation_edges"] == 0


def test_nebula_verified_filter() -> None:
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _allow(connection, tenant_id, f"nebula-verified-{uuid4().hex[:8]}")
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    resp = client.get(PATH + "?lifecycle=verified", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["stats"]["total"] == 100
    assert all(n["lifecycle"] == "verified" for n in body["nodes"])


def test_nebula_domain_scopes_graph_and_unknown_domain_is_400() -> None:
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _allow(connection, tenant_id, f"nebula-domain-{uuid4().hex[:8]}")
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}

    # a real domain pack scopes the live graph to that pack's plugins
    quant = client.get(PATH + "?domain=quant", headers=headers)
    assert quant.status_code == 200, quant.text
    body = quant.json()
    assert body["stats"]["total"] == 5  # quant pack: 5 plugins
    quant_ids = {node["id"] for node in body["nodes"]}
    assert all(node_id.startswith("quant.") for node_id in quant_ids)

    # the four packs partition the directory — quant is disjoint from audit,
    # so the default graph really is a different plugin set, not a re-render
    audit = client.get(PATH, headers=headers).json()
    assert quant_ids.isdisjoint({node["id"] for node in audit["nodes"]})

    # unknown domain is a client error, never a 500 with a stack trace
    bad = client.get(PATH + "?domain=does_not_exist", headers=headers)
    assert bad.status_code == 400


def test_nebula_rejects_missing_and_unknown_tenant() -> None:
    client = TestClient(create_app(Settings(database_url=DB)))
    # missing tenant header -> validation error, never an anonymous graph
    assert client.get(PATH).status_code == 422
    # unknown tenant is rejected, not silently scoped to nothing
    foreign = client.get(PATH, headers={"X-Tenant-Id": str(uuid4()), "X-Trace-Id": str(uuid4())})
    assert foreign.status_code in (403, 404, 409)
