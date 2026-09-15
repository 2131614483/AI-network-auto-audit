"""Plugin lifecycle view and its API.

The view exists to keep one specific confusion out of the UI: a plugin can be
*declared* non-executable while still sitting in the runtime allow list, which
is a real third state and not something to average away.  These tests pin the
invariants rather than the counts, because the plugin directory grows.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.catalog.lifecycle import plugin_lifecycle

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        return UUID(str(cur.fetchone()[0]))


def test_lifecycle_view_reports_declared_permitted_and_published() -> None:
    view = plugin_lifecycle(DB)
    summary = view["summary"]
    items = view["items"]

    assert summary["plugins"] == len(items) > 0
    assert summary["protocol_verified"] + summary["protocol_contract_only"] == summary["plugins"]

    by_id = {item["plugin_id"]: item for item in items}
    for item in items:
        assert item["conflict"] == ((item["protocol_lifecycle"] == "verified") != item["executable"])
        assert item["protocol_lifecycle"] in {"verified", "contract_only"}
        assert isinstance(item["domains"], list) and item["domains"]
    assert by_id["knowledge.document-ingestion"]["version"] == "0.1.0"

    assert summary["conflict"] == sum(1 for item in items if item["conflict"])
    assert summary["executable"] == sum(1 for item in items if item["executable"])
    assert summary["allow_list_size"] >= summary["executable"]
    assert summary["allow_list_unmatched"] == []
    assert summary["version_registered"] == sum(1 for item in items if item["version"])
    assert sum(summary["by_domain"].values()) == summary["plugins"]


def test_lifecycle_filters_do_not_shrink_the_summary() -> None:
    full = plugin_lifecycle(DB)
    cut = plugin_lifecycle(DB, domain="knowledge")

    assert cut["summary"] == full["summary"], "a filtered view must not re-describe the whole corpus"
    assert 0 < len(cut["items"]) < len(full["items"]), "domain filter silently did nothing"
    assert all("knowledge" in item["domains"] for item in cut["items"])

    conflicts = plugin_lifecycle(DB, conflict_only=True)
    assert conflicts["summary"] == full["summary"]
    assert all(item["conflict"] for item in conflicts["items"])
    assert len(conflicts["items"]) == full["summary"]["conflict"]

    verified_only = plugin_lifecycle(DB, lifecycle="verified")
    assert all(item["protocol_lifecycle"] == "verified" for item in verified_only["items"])
    assert len(verified_only["items"]) == full["summary"]["protocol_verified"]

    combined = plugin_lifecycle(DB, domain="knowledge", conflict_only=True)
    assert all(item["conflict"] and "knowledge" in item["domains"] for item in combined["items"])


def test_lifecycle_api_respects_policy_and_expresses_conflicts() -> None:
    tenant_id = _tenant_id()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s) "
            "ON CONFLICT (tenant_id,name,version) DO UPDATE SET status='active',rules=EXCLUDED.rules",
            (
                tenant_id,
                f"lifecycle-api-{uuid4().hex}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["plugin.lifecycle.read"]}}]),
            ),
        )

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}

    response = client.get("/api/v1/plugins/lifecycle", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["plugins"] >= len(body["items"]) > 0
    assert body["trace_id"] == headers["X-Trace-Id"]
    assert all("conflict" in item and "protocol_lifecycle" in item for item in body["items"])

    filtered = client.get("/api/v1/plugins/lifecycle", headers=headers, params={"conflict_only": "true"}).json()
    assert filtered["summary"] == body["summary"]
    assert all(item["conflict"] for item in filtered["items"])
    assert len(filtered["items"]) == body["summary"]["conflict"]

    bad_lifecycle = client.get("/api/v1/plugins/lifecycle", headers=headers, params={"lifecycle": "nonsense"})
    assert bad_lifecycle.status_code == 422
