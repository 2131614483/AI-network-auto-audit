"""The seven-ring evolution view and its API.

The rings mix two kinds of fact: row counts (rings 1, 3-6) and code facts
(rings 2 and 7).  The code-fact rings are the reason this module exists — they
are exactly the ones that look connected in prose while being unconnected in
reality, so the tests pin the *method* (AST, not text search) as well as the
statuses.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.experience.evolution import _ring2_wiring, _ring7_readpath, evolution_status

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
ROOT = Path(__file__).resolve().parents[2]


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        return UUID(str(cur.fetchone()[0]))


def test_ring2_wiring_is_a_parse_not_a_search() -> None:
    result = _ring2_wiring(ROOT)
    assert result["wired"] is True, "the projector is wired into start_plan_run; a miss here is a regression"
    assert "start_plan_run" in result["checked"]


def test_ring7_readpath_counts_imports_not_prose(tmp_path: Path) -> None:
    planner = tmp_path / "packages" / "ai_planner"
    planner.mkdir(parents=True)
    (planner / "planner.py").write_text(
        '"""Knowledge is mentioned here but never imported."""\n'
        "from packages.ai_planner import composer\n"
        "import packages.control.scheduler\n",
        encoding="utf-8",
    )
    (planner / "wired.py").write_text(
        "from packages.experience.statistics import rollup\n",
        encoding="utf-8",
    )
    result = _ring7_readpath(tmp_path)
    assert result["imported"] is True
    assert len(result["imports"]) == 1, "a docstring mention must not count as integration"
    assert "packages.experience" in result["imports"][0]
    assert result["files_parsed"] == 2


def test_evolution_status_is_self_consistent() -> None:
    view = evolution_status(DB, "local-dev", ROOT)
    rings, summary = view["rings"], view["summary"]

    assert [ring["ring"] for ring in rings] == list(range(1, 8))
    assert all(ring["status"] in {"ok", "partial", "broken"} for ring in rings)
    assert all(ring["checked"] for ring in rings), "every status names the check that produced it"

    assert summary["ok"] + summary["partial"] + summary["broken"] == 7
    assert summary["loop_closed"] == (summary["broken"] == 0 and summary["partial"] == 0)

    first_not_ok = next((ring["name"] for ring in rings if ring["status"] != "ok"), None)
    assert view["weakest"] == first_not_ok

    # Ring 2 must agree with the parse it is derived from.
    ring2 = next(ring for ring in rings if ring["ring"] == 2)
    assert ring2["evidence"]["wired_into_dag_path"] == _ring2_wiring(ROOT)["wired"]

    # Ring 6 is broken or ok on a real, checkable quantity.
    ring6 = next(ring for ring in rings if ring["ring"] == 6)
    assert ring6["status"] == ("ok" if ring6["evidence"]["experience_change_sets"] > 0 else "broken")


def test_evolution_api_respects_policy() -> None:
    tenant = _tenant_id()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s) "
            "ON CONFLICT (tenant_id,name,version) DO UPDATE SET status='active',rules=EXCLUDED.rules",
            (
                tenant,
                f"evolution-api-{uuid4().hex}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["experience.evolution.read"]}}]),
            ),
        )

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant), "X-Trace-Id": str(uuid4())}
    response = client.get("/api/v1/experience/evolution", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == headers["X-Trace-Id"]
    assert len(body["rings"]) == 7
    assert body["summary"]["ok"] + body["summary"]["partial"] + body["summary"]["broken"] == 7
