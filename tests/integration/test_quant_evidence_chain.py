"""Phase 8: read-only quant evidence-chain queries and policy gating.

The CSV simulated backtest itself is covered by ``test_quant_backtest``; this
suite proves the readable lineage (backtest -> dataset -> snapshot with code/data
SHA and the point-in-time gate) and the policy-gated read endpoints.  No write
path is added in this phase.
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
from packages.plugin_runtime.registration import publish_quant_evidence_chain_allow_policy
from packages.quant.backtest import run_csv_backtest
from packages.quant.service import QuantService

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _allow(connection, tenant_id: UUID, name: str, capabilities: list[str], risk_classes: list[str]) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (tenant_id, name, Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": capabilities, "risk_classes": risk_classes}}])),
        )


def _make_prices(tmp_path: Path) -> Path:
    prices = tmp_path / "prices.csv"
    prices.write_text("date,close\n2026-01-01,100\n2026-01-02,105\n2026-01-03,102\n2026-01-04,108\n", encoding="utf-8")
    return prices


def _run_backtest(tmp_path: Path, strategy_key: str = "daily_momentum") -> str:
    result = run_csv_backtest(DB, _make_prices(tmp_path), strategy_key=strategy_key)
    return result.backtest_id


def test_lineage_read_returns_full_chain(tmp_path: Path) -> None:
    backtest_id = _run_backtest(tmp_path, "daily_momentum")
    lineage = QuantService(DB).get_backtest_lineage(backtest_id)
    assert lineage["backtest"]["status"] == "completed"
    assert lineage["backtest"]["parameters"]["point_in_time_gate"] == "passed"
    assert lineage["backtest"]["parameters"]["code_sha256"]
    assert lineage["backtest"]["metrics"]["simulated_only"] is True
    assert lineage["backtest"]["metrics"]["total_return"] != 0
    assert lineage["dataset"]["key"].startswith("csv:")
    assert lineage["dataset"]["freshness_status"] == "fresh"
    assert lineage["dataset"]["metadata"]["source_sha256"]
    assert lineage["dataset"]["metadata"]["row_count"] == 4

    summaries = QuantService(DB).list_backtests()
    row = next(item for item in summaries if item["id"] == backtest_id)
    assert row["strategy_key"] == "daily_momentum"
    assert row["dataset_key"].startswith("csv:")
    assert row["observations"] == 4


def test_unknown_backtest_and_bad_limit_are_rejected(tmp_path: Path) -> None:
    _run_backtest(tmp_path)
    service = QuantService(DB)
    with pytest.raises(ValueError, match="not found"):
        service.get_backtest_lineage(str(uuid4()))
    with pytest.raises(ValueError, match="limit"):
        service.list_backtests(limit=0)
    with pytest.raises(ValueError, match="limit"):
        service.list_backtests(limit=501)


def test_api_endpoints_are_policy_gated(tmp_path: Path) -> None:
    backtest_id = _run_backtest(tmp_path, "api-gate")
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            """UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s""",
            (tenant_id, "%quant.chain.read%"),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    denied_list = client.get("/api/v1/quant/backtests", headers=headers)
    assert denied_list.status_code in (403, 409)
    denied_lineage = client.get(f"/api/v1/quant/backtests/{backtest_id}/lineage", headers=headers)
    assert denied_lineage.status_code in (403, 409)

    with psycopg2.connect(DB) as connection:
        _allow(connection, tenant_id, f"p8-{uuid4().hex[:8]}", ["quant.chain.read"], ["read_only"])
    ok_list = client.get("/api/v1/quant/backtests?limit=10", headers=headers)
    assert ok_list.status_code == 200
    assert any(item["id"] == backtest_id for item in ok_list.json()["items"])
    ok_lineage = client.get(f"/api/v1/quant/backtests/{backtest_id}/lineage", headers=headers)
    assert ok_lineage.status_code == 200
    body = ok_lineage.json()
    assert body["backtest_id"] == backtest_id
    assert body["backtest"]["parameters"]["point_in_time_gate"] == "passed"


def test_publish_policy_registers_phase8_capability() -> None:
    tenant_id = publish_quant_evidence_chain_allow_policy(DB)
    assert UUID(str(tenant_id))
    from packages.plugin_runtime.registration import _PHASE8_POLICY_NAME

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "SELECT rules FROM policy.policy_sets WHERE tenant_id=%s AND name=%s AND status='active'",
            (tenant_id, _PHASE8_POLICY_NAME),
        )
        rules = cur.fetchone()[0]
        caps = {r["match"]["capabilities"][0] for r in rules}
        assert {"quant.chain.read"} == caps