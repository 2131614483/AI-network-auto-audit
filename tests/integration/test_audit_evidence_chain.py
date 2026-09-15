"""Phase 7: audit evidence-chain reads and governed finding confirmation.

The CSV ledger pipeline is already covered by ``test_audit_pipeline``; this suite
proves the readable lineage (engagements -> evidence -> anomaly candidates ->
findings with claims) and the idempotent, policy-gated confirmation endpoint.
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
from packages.audit.pipeline import AuditPipeline
from packages.plugin_runtime.registration import publish_audit_evidence_chain_allow_policy

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


def _make_ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("account,amount\n6001,1200000\n6002,bad\n", encoding="utf-8")
    return ledger


def _run_ledger(tmp_path: Path, engagement_name: str = "P7 Ledger") -> str:
    result = AuditPipeline(DB).run_ledger_csv(_make_ledger(tmp_path), engagement_name=engagement_name)
    return result.engagement_id


def test_lineage_read_returns_full_chain(tmp_path: Path) -> None:
    engagement_id = _run_ledger(tmp_path, "Lineage full")
    pipeline = AuditPipeline(DB)
    lineage = pipeline.get_engagement_lineage(engagement_id)
    assert lineage["engagement"]["status"] == "completed"
    assert len(lineage["evidence"]) == 1
    assert lineage["evidence"][0]["evidence_type"] == "ledger_source"
    assert {a["rule_key"] for a in lineage["anomalies"]} == {"missing_or_invalid_amount", "large_amount"}
    engagements = pipeline.list_engagements()
    assert any(e["id"] == engagement_id and e["evidence_count"] == 1 for e in engagements)


def test_confirm_is_idempotent_per_candidate(tmp_path: Path) -> None:
    engagement_id = _run_ledger(tmp_path, "Idempotent confirm")
    pipeline = AuditPipeline(DB)
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute("SELECT id FROM audit.anomaly_candidates WHERE engagement_id=%s ORDER BY score DESC LIMIT 1", (engagement_id,))
        candidate_id = str(cur.fetchone()[0])
    first = pipeline.confirm_candidate(candidate_id, reviewer_label="independent-qa")
    second = pipeline.confirm_candidate(candidate_id, reviewer_label="independent-qa")
    assert first == second
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute("SELECT count(*) FROM audit.findings WHERE tenant_id=%s AND engagement_id=%s", (tenant_id, engagement_id))
        assert cur.fetchone()[0] == 1
    lineage = pipeline.get_engagement_lineage(engagement_id)
    assert any(f["id"] == first and f["status"] == "confirmed" for f in lineage["findings"])
    assert any(f["claims"][0]["reviewer_label"] == "independent-qa" for f in lineage["findings"] if f["claims"])


def test_confirm_rejects_blank_reviewer_and_unknown_candidate(tmp_path: Path) -> None:
    engagement_id = _run_ledger(tmp_path, "Reject env")
    pipeline = AuditPipeline(DB)
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _tenant(connection)
        cur.execute("SELECT id FROM audit.anomaly_candidates WHERE engagement_id=%s LIMIT 1", (engagement_id,))
        candidate_id = str(cur.fetchone()[0])
    with pytest.raises(ValueError):
        pipeline.confirm_candidate(candidate_id, reviewer_label="   ")
    with pytest.raises(ValueError):
        pipeline.confirm_candidate(str(uuid4()), reviewer_label="qa")


def test_api_endpoints_are_policy_gated_and_confirm_works(tmp_path: Path) -> None:
    engagement_id = _run_ledger(tmp_path, "API confirm")
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute("SELECT id FROM audit.anomaly_candidates WHERE engagement_id=%s ORDER BY score DESC LIMIT 1", (engagement_id,))
        candidate_id = str(cur.fetchone()[0])
        cur.execute(
            """UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s
            AND (rules::text LIKE '%%audit.finding.confirm%%'
                 OR rules::text LIKE '%%audit.chain.read%%')""",
            (tenant_id,),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    read_headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    # read not yet granted -> lineage refused
    denied = client.get(f"/api/v1/audit/engagements/{engagement_id}/lineage", headers=read_headers)
    assert denied.status_code in (403, 409)
    write_headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4()), "Idempotency-Key": str(uuid4())}
    denied_confirm = client.post(
        "/api/v1/audit/findings/confirm", json={"candidate_id": candidate_id, "reviewer_label": "qa"}, headers=write_headers
    )
    assert denied_confirm.status_code in (403, 409)

    with psycopg2.connect(DB) as connection:
        _allow(connection, tenant_id, f"p7-{uuid4().hex[:8]}", ["audit.chain.read"], ["read_only"])
        _allow(connection, tenant_id, f"p7-{uuid4().hex[:8]}-w", ["audit.finding.confirm"], ["medium"])
    ok = client.get(f"/api/v1/audit/engagements/{engagement_id}/lineage", headers=read_headers)
    assert ok.status_code == 200
    body = ok.json()
    assert body["engagement"]["id"] == engagement_id
    assert len(body["evidence"]) == 1

    ok_confirm = client.post(
        "/api/v1/audit/findings/confirm", json={"candidate_id": candidate_id, "reviewer_label": "qa"},
        headers={"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4()), "Idempotency-Key": str(uuid4())},
    )
    assert ok_confirm.status_code == 200
    assert ok_confirm.json()["status"] == "confirmed"
    assert ok_confirm.json()["finding_id"]


def test_publish_policy_registers_phase7_capabilities() -> None:
    tenant_id = publish_audit_evidence_chain_allow_policy(DB)
    assert UUID(str(tenant_id))
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        from packages.plugin_runtime.registration import _PHASE7_POLICY_NAME

        cur.execute("SELECT rules FROM policy.policy_sets WHERE tenant_id=%s AND name=%s AND status='active'", (tenant_id, _PHASE7_POLICY_NAME))
        rules = cur.fetchone()[0]
        caps = {r["match"]["capabilities"][0] for r in rules}
        assert {"audit.chain.read", "audit.finding.confirm"} == caps