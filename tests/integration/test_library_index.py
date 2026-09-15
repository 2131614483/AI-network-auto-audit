"""Read-only document asset index view and its API.

The index is a filesystem projection, not a second corpus: it must only expose
metadata (path/title/kind/size/mtime) and never document bodies.  Filters must
narrow the items while leaving the corpus summary untouched.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.library.index import library_index

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
                f"library-api-{uuid4().hex}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": capabilities}}]),
            ),
        )


def test_index_only_lists_markdown_metadata() -> None:
    view = library_index(ROOT)
    summary, items = view["summary"], view["items"]

    assert summary["total"] == len(items) > 0
    assert sum(summary["by_kind"].values()) == summary["total"]
    assert sum(summary["by_project"].values()) == summary["total"]

    for item in items:
        assert item["path"].endswith(".md")
        assert item["title"]
        assert item["size_bytes"] > 0
        assert "content" not in item and "body" not in item, "the index must not carry document text"
        assert set(item) == {
            "path",
            "title",
            "kind",
            "project",
            "size_bytes",
            "modified_at",
            "related_run",
            "related_case",
        }


def test_filters_do_not_shrink_the_summary() -> None:
    full = library_index(ROOT)

    cases = library_index(ROOT, kind="案例资料")
    assert cases["summary"] == full["summary"]
    assert 0 < len(cases["items"]) < len(full["items"])
    assert all(item["kind"] == "案例资料" for item in cases["items"])
    assert all(item["path"].startswith("审计项目案例/") for item in cases["items"])
    # Files directly under the cases root (not inside a named case) honestly
    # report related_case=None instead of inventing one.
    assert any(item["related_case"] for item in cases["items"])

    keyword = library_index(ROOT, q="审计报告")
    assert keyword["summary"] == full["summary"]
    assert all(
        "审计报告" in item["path"] or "审计报告" in item["title"] for item in keyword["items"]
    )

    project = library_index(ROOT, project="茅台审计报告分析")
    assert project["summary"] == full["summary"]
    assert all(item["project"] == "茅台审计报告分析" for item in project["items"])


def test_library_api_respects_policy_and_validates() -> None:
    _grant(["library.index.read"])
    tenant = _tenant_id()
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant), "X-Trace-Id": str(uuid4())}

    response = client.get("/api/v1/library/index", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == headers["X-Trace-Id"]
    assert body["summary"]["total"] == len(body["items"])
    assert all(item["path"].endswith(".md") for item in body["items"])

    bad_kind = client.get("/api/v1/library/index", headers=headers, params={"kind": "nonsense"})
    assert bad_kind.status_code == 422
