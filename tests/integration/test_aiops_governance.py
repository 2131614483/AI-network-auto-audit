"""Phase 9 local-simulation-only AIOps lineage and Canary governance."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.aiops.engine import AIOpsEngine
from packages.aiops.service import AIOpsGovernanceService
from packages.plugin_runtime.registration import publish_aiops_governance_allow_policy
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
                Json([{
                    "rule_id": str(uuid4()), "effect": "allow",
                    "match": {
                        "capabilities": [capability], "risk_classes": [risk_class],
                        "side_effects": ["read_only" if risk_class == "read_only" else "write_data"],
                    },
                }]),
            ),
        )


def _make_completed_canary() -> tuple[str, str]:
    marker = uuid4().hex
    engine = AIOpsEngine(DB, PolicyEngine(allow=["aiops.playbook.restart-worker"]))
    incident_id = engine.ingest_alert("phase9-monitor", f"canary-{marker}", "medium", {"marker": marker})
    proposal_id = engine.propose(
        incident_id, "restart-worker", {"kind": "restart", "target": "local-simulated-worker"}, risk_class="medium"
    )
    change_request_id = engine.request_change(proposal_id)
    engine.approve_change(change_request_id)
    execution_id, status = engine.execute(proposal_id, mode="canary", change_request_id=change_request_id)
    assert status == "completed"
    return str(incident_id), str(execution_id)


def test_incident_lineage_is_tenant_scoped_and_explicitly_simulated() -> None:
    incident_id, execution_id = _make_completed_canary()
    service = AIOpsGovernanceService(DB)
    summaries = service.list_incidents()
    summary = next(item for item in summaries if item["id"] == incident_id)
    assert summary["proposal_count"] == 1
    assert summary["change_request_count"] == 1
    assert summary["execution_count"] == 1

    lineage = service.get_incident_lineage(incident_id)
    assert lineage["kind"] == "aiops_governance_chain"
    assert lineage["simulated_only"] is True
    assert lineage["incident"]["id"] == incident_id
    assert len(lineage["alerts"]) == 1
    assert lineage["proposals"][0]["playbook_key"] == "restart-worker"
    assert lineage["change_requests"][0]["status"] == "approved"
    assert lineage["executions"][0]["id"] == execution_id
    assert lineage["executions"][0]["mode"] == "canary"
    assert lineage["executions"][0]["simulated"] is True
    assert lineage["verifications"] == []


def test_canary_verification_is_ledgered_idempotent_and_fail_closed() -> None:
    _incident_id, execution_id = _make_completed_canary()
    service = AIOpsGovernanceService(DB)
    trace_id = str(uuid4())
    first = service.verify_canary(execution_id, "healthy", "phase9-reviewer", trace_id, "p9-key-healthy")
    replay = service.verify_canary(execution_id, "healthy", "phase9-reviewer", trace_id, "p9-key-replay")
    assert first["status"] == "verified"
    assert first["idempotent"] is False
    assert replay["verification_id"] == first["verification_id"]
    assert replay["idempotent"] is True
    with pytest.raises(ValueError, match="already has a final verification"):
        service.verify_canary(execution_id, "rollback", "phase9-reviewer", str(uuid4()), "p9-key-conflict")

    _incident_id, rollback_execution_id = _make_completed_canary()
    rollback = service.verify_canary(
        rollback_execution_id, "rollback", "phase9-reviewer", str(uuid4()), "p9-key-rollback"
    )
    assert rollback["status"] == "rolled_back"
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            """SELECT p.status FROM aiops.executions e JOIN aiops.remediation_proposals p ON p.id=e.proposal_id
               WHERE e.tenant_id=%s AND e.id=%s""",
            (tenant_id, rollback_execution_id),
        )
        assert cur.fetchone()[0] == "circuit_open"


def test_bad_limits_unknown_ids_and_non_canary_verification_are_rejected() -> None:
    service = AIOpsGovernanceService(DB)
    with pytest.raises(ValueError, match="live execution is not implemented"):
        AIOpsEngine(DB).execute(uuid4(), mode="live")
    with pytest.raises(ValueError, match="limit"):
        service.list_incidents(0)
    with pytest.raises(ValueError, match="limit"):
        service.list_incidents(501)
    with pytest.raises(ValueError, match="not found"):
        service.get_incident_lineage(str(uuid4()))
    with pytest.raises(ValueError, match="not found"):
        service.verify_canary(str(uuid4()), "healthy", "phase9-reviewer", str(uuid4()), "p9-key-missing")


def test_api_is_policy_gated_and_requires_idempotency_for_verification() -> None:
    incident_id, execution_id = _make_completed_canary()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%aiops.%"),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    assert client.get("/api/v1/aiops/incidents", headers=headers).status_code in (403, 409)
    assert client.get(f"/api/v1/aiops/incidents/{incident_id}/lineage", headers=headers).status_code in (403, 409)
    assert client.post(
        f"/api/v1/aiops/executions/{execution_id}/verify",
        headers=headers,
        json={"outcome": "healthy", "reviewer_label": "phase9-reviewer"},
    ).status_code in (403, 409, 422)

    with psycopg2.connect(DB) as connection:
        _allow(connection, tenant_id, f"p9-read-{uuid4().hex[:8]}", "aiops.chain.read", "read_only")
        _allow(connection, tenant_id, f"p9-write-{uuid4().hex[:8]}", "aiops.canary.verify", "medium")
    listed = client.get("/api/v1/aiops/incidents?limit=10", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == incident_id for item in listed.json()["items"])
    lineage = client.get(f"/api/v1/aiops/incidents/{incident_id}/lineage", headers=headers)
    assert lineage.status_code == 200
    assert lineage.json()["simulated_only"] is True
    missing_key = client.post(
        f"/api/v1/aiops/executions/{execution_id}/verify",
        headers=headers,
        json={"outcome": "healthy", "reviewer_label": "phase9-reviewer"},
    )
    assert missing_key.status_code == 422
    verified = client.post(
        f"/api/v1/aiops/executions/{execution_id}/verify",
        headers={**headers, "Idempotency-Key": f"p9-api-{uuid4().hex}"},
        json={"outcome": "healthy", "reviewer_label": "phase9-reviewer"},
    )
    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"


def test_publish_policy_registers_only_phase9_capabilities() -> None:
    tenant_id = publish_aiops_governance_allow_policy(DB)
    assert UUID(str(tenant_id))
    from packages.plugin_runtime.registration import _PHASE9_POLICY_NAME

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "SELECT rules FROM policy.policy_sets WHERE tenant_id=%s AND name=%s AND status='active'",
            (tenant_id, _PHASE9_POLICY_NAME),
        )
        rules = cur.fetchone()[0]
        capabilities = {rule["match"]["capabilities"][0] for rule in rules}
        assert capabilities == {"aiops.chain.read", "aiops.canary.verify"}
