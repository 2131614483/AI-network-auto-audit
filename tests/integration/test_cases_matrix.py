"""Case x stage matrix view and its API.

Every case must always report all nine stages -- an empty stage is count=0,
never a missing column -- and the report table must carry its derived quality
fields.  These tests pin the shape, not the file counts, because the corpus grows.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.cases.matrix import STAGES, case_matrix

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
                f"cases-api-{uuid4().hex}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": capabilities}}]),
            ),
        )


def test_matrix_returns_three_cases_with_nine_stages_each() -> None:
    view = case_matrix(ROOT)
    cases = view["cases"]
    assert len(cases) == 3

    expected_keys = [key for key, _ in STAGES]
    for case in cases:
        assert len(case["stages"]) == 9
        assert [stage["key"] for stage in case["stages"]] == expected_keys
        assert all(stage["count"] >= 0 for stage in case["stages"])
        assert case["file_count"] == sum(stage["count"] for stage in case["stages"])
        assert case["report_count"] == len(case["reports"])
        for report in case["reports"]:
            assert set(report) == {"path", "lines", "bytes", "modified_at", "m7_9_share", "v11_ok"}
            assert report["path"].endswith(".md")
            assert report["lines"] > 0
            assert 0.0 <= report["m7_9_share"] <= 1.0
            assert isinstance(report["v11_ok"], bool)


def test_cases_api_respects_policy() -> None:
    _grant(["cases.read"])
    tenant = _tenant_id()
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant), "X-Trace-Id": str(uuid4())}

    response = client.get("/api/v1/cases", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == headers["X-Trace-Id"]
    assert len(body["cases"]) == 3
    for case in body["cases"]:
        assert len(case["stages"]) == 9
        assert case["file_count"] == sum(stage["count"] for stage in case["stages"])
