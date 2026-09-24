"""连通性只读视图与它的 API。

三件事必须成立，否则这个视图会变成"看起来有数据、其实在撒谎"：

1. **自洽**：指标、孤岛总数、分类计数、明细行数四者必须对得上；
2. **口径必须随结果返回**：不同口径（lifecycle / 是否计入 invokes）的数字不可比，
   界面若拿不到口径就会把两组数字并排展示；
3. **页面口径与脚本口径是同一份实现** —— 否则看板与 CI 基线闸门会对同一张网给出相反结论。
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.ai_planner.connectivity import ISLAND_CATEGORIES
from packages.catalog.connectivity_view import connectivity_report

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
ROOT = Path(__file__).resolve().parents[2]


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
    assert row is not None, "local-dev tenant must be seeded by the migrations"
    return UUID(str(row[0]))


def _grant(capabilities: list[str]) -> None:
    tenant = _tenant_id()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s) "
            "ON CONFLICT (tenant_id,name,version) DO UPDATE SET status='active',rules=EXCLUDED.rules",
            (
                tenant,
                f"connectivity-api-{uuid4().hex}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": capabilities}}]),
            ),
        )


# -- 视图本身 --------------------------------------------------------------


def test_report_is_internally_consistent() -> None:
    report = connectivity_report()
    metrics, islands = report["metrics"], report["islands"]

    assert metrics["plugins"] > 0
    assert metrics["edges"] > 0
    assert metrics["component_sizes"], "分量规模不能为空——弱连通分量数要靠它解释"
    assert len(metrics["component_sizes"]) == metrics["weakly_connected_components"]

    assert islands["total"] == len(islands["items"])
    assert islands["total"] == sum(entry["count"] for entry in islands["present"])
    assert all(entry["count"] > 0 for entry in islands["present"]), "present 里不该出现 0 计数的分类"
    assert {entry["category"] for entry in islands["present"]} <= set(ISLAND_CATEGORIES)


def test_every_island_carries_a_reason() -> None:
    """孤岛必须给依据——"没连上"不是分类，只是现象。"""
    for island in connectivity_report()["islands"]["items"]:
        assert island["plugin_id"]
        assert island["category"] in ISLAND_CATEGORIES
        assert island["category_zh"], "分类必须有中文标签，界面直接显示它"
        assert island["reason"].strip(), f"{island['plugin_id']} 没有给出分类依据"


def test_scope_travels_with_the_numbers() -> None:
    """口径随结果返回；界面据此显示，避免拿不可比的数字并排展示。"""
    full = connectivity_report()
    assert full["scope"] == {"lifecycle": None, "include_invokes": False, "label": "lifecycle=全部 · 图口径=contract"}
    assert full["metrics"]["edge_set"] == "contract"

    with_invokes = connectivity_report(include_invokes=True)
    assert with_invokes["scope"]["include_invokes"] is True
    assert with_invokes["metrics"]["edge_set"] == "contract+invokes"

    verified = connectivity_report(lifecycle="verified")
    assert verified["scope"]["lifecycle"] == "verified"
    assert verified["scope"]["label"] == "lifecycle=verified · 图口径=contract"


def test_include_invokes_actually_connects_more() -> None:
    """invokes 跨层调用是真实存在的连接：计入后边数必须增加、悬空必须减少。

    这条断言同时钉住"include_invokes 不是空开关"——若哪天它退化成无操作，这里会红。
    """
    contract_only = connectivity_report()
    with_invokes = connectivity_report(include_invokes=True)

    assert with_invokes["metrics"]["plugins"] == contract_only["metrics"]["plugins"]
    assert with_invokes["metrics"]["edges"] > contract_only["metrics"]["edges"]
    assert with_invokes["metrics"]["isolated_nodes"] < contract_only["metrics"]["isolated_nodes"]


def test_unknown_lifecycle_is_rejected() -> None:
    with pytest.raises(ValueError):
        connectivity_report(lifecycle="bogus")


# -- API -------------------------------------------------------------------


def test_endpoint_respects_policy_and_validates() -> None:
    _grant(["connectivity.report.read"])
    tenant = _tenant_id()
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant), "X-Trace-Id": str(uuid4())}

    response = client.get("/api/v1/connectivity/report", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == headers["X-Trace-Id"]
    assert body["scope"]["label"] == "lifecycle=全部 · 图口径=contract"
    assert body["islands_total"] == sum(entry["count"] for entry in body["islands_present"])
    assert len(body["islands"]) == body["islands_total"]
    assert body["metrics"]["plugins"] > 0

    scoped = client.get(
        "/api/v1/connectivity/report", headers=headers, params={"lifecycle": "verified", "include_invokes": "true"}
    )
    assert scoped.status_code == 200
    assert scoped.json()["metrics"]["edge_set"] == "contract+invokes"

    bad = client.get("/api/v1/connectivity/report", headers=headers, params={"lifecycle": "nonsense"})
    assert bad.status_code == 422


def test_endpoint_fails_closed_without_the_capability() -> None:
    """读也是一次访问：没有 CAP 必须被策略网关拒绝，而不是"页面看起来坏了"。"""
    tenant = _tenant_id()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE name LIKE 'connectivity-api-%'"
        )
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' "
            "WHERE name='local-connectivity-read' AND version=1"
        )
    try:
        client = TestClient(create_app(Settings(database_url=DB)))
        response = client.get(
            "/api/v1/connectivity/report",
            headers={"X-Tenant-Id": str(tenant), "X-Trace-Id": str(uuid4())},
        )
        assert response.status_code == 409
    finally:
        # 复原迁移种下的那条，避免影响后续用例（app 角色无 DELETE 权限，只能改 status）。
        with psycopg2.connect(DB) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            cur.execute(
                "UPDATE policy.policy_sets SET status='active' "
                "WHERE name='local-connectivity-read' AND version=1"
            )
