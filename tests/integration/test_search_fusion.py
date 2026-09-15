"""Unified cross-kind search view and its API.

The view fuses four independent sources with the existing reciprocal-rank
formula and must never fake vector availability.  These tests pin the honest
behaviour: an empty result list is normal, ``degraded.vector`` is a truthful
bool, a seeded suggestion and an on-disk bundle are both findable, and the API
layer enforces the contract (trace echo, kinds validation, required query).
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.search.fusion import search_all

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
ROOT = Path(__file__).resolve().parents[2]


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        return UUID(str(cur.fetchone()[0]))


def _grant(capabilities: list[str]) -> None:
    tenant = _tenant_id()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s) "
            "ON CONFLICT (tenant_id,name,version) DO UPDATE SET status='active',rules=EXCLUDED.rules",
            (
                tenant,
                f"search-api-{uuid4().hex}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": capabilities}}]),
            ),
        )


def _seed_suggestion(plugin_id: str) -> None:
    tenant = _tenant_id()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.execute(
            "INSERT INTO experience.relation_suggestions(tenant_id,source_plugin_id,target_plugin_id,contract_id) "
            "VALUES(%s,%s,%s,%s)",
            (tenant, plugin_id, "audit.probe-target", "contract-search-probe"),
        )


def _first_bundle_run_id() -> str | None:
    evidence = ROOT / ".data" / "evidence"
    if not evidence.is_dir():
        return None
    zips = sorted(evidence.glob("*.zip"))
    return zips[0].stem if zips else None


def test_search_empty_query_is_rejected_and_degraded_is_honest() -> None:
    try:
        search_all(DB, "local-dev", ROOT, "   ")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a blank query must not search")

    view = search_all(DB, "local-dev", ROOT, f"no-such-token-{uuid4().hex}")
    assert view["items"] == []
    assert isinstance(view["degraded"]["vector"], bool)
    assert set(view) == {"items", "degraded"}


def test_search_finds_seeded_suggestion_and_on_disk_artifact() -> None:
    plugin_id = f"audit.search-probe-{uuid4().hex[:8]}"
    _seed_suggestion(plugin_id)

    view = search_all(DB, "local-dev", ROOT, plugin_id)
    kinds = {item["kind"] for item in view["items"]}
    assert "suggestion" in kinds
    suggestion = next(item for item in view["items"] if item["kind"] == "suggestion")
    assert suggestion["anchor"] == {
        "schema": "experience",
        "table": "relation_suggestions",
        "pk": suggestion["item_id"],
    }
    assert suggestion["matched_by"]

    run_id = _first_bundle_run_id()
    assert run_id is not None, "the repo ships evidence bundles; the artifact source needs them"
    artifact_view = search_all(DB, "local-dev", ROOT, run_id[:8])
    artifact = next(item for item in artifact_view["items"] if item["kind"] == "artifact")
    assert artifact["anchor"]["path"].endswith(f"{run_id}.zip")
    assert len(artifact["anchor"]["sha256"]) == 12

    seen = kinds | {item["kind"] for item in artifact_view["items"]}
    assert "artifact" in seen and "suggestion" in seen, "cross-kind fusion must surface more than one source"


def test_kinds_filter_restricts_to_requested_sources() -> None:
    plugin_id = f"audit.search-probe-{uuid4().hex[:8]}"
    _seed_suggestion(plugin_id)
    view = search_all(DB, "local-dev", ROOT, plugin_id, kinds={"suggestion"})
    assert view["items"]
    assert all(item["kind"] == "suggestion" for item in view["items"])

    try:
        search_all(DB, "local-dev", ROOT, plugin_id, kinds={"nonsense"})
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("an unknown kind must be rejected")


def test_search_api_respects_policy_and_validates() -> None:
    _grant(["search.read"])
    tenant = _tenant_id()
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant), "X-Trace-Id": str(uuid4())}

    ok = client.get("/api/v1/search", headers=headers, params={"q": "audit"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["trace_id"] == headers["X-Trace-Id"]
    assert "vector" in body["degraded"]
    for item in body["items"]:
        assert item["kind"] in {"document", "run", "artifact", "suggestion"}
        assert set(item) == {"kind", "item_id", "title", "subtitle", "anchor", "score", "matched_by"}

    missing = client.get("/api/v1/search", headers=headers)
    assert missing.status_code == 422

    blank = client.get("/api/v1/search", headers=headers, params={"q": ""})
    assert blank.status_code == 422
