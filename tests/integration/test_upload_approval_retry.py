"""Exercise the real gateway, approval and ingestion chain in isolated tenants."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app


@pytest.fixture
def flow(tmp_path: Path, monkeypatch):
    database = os.environ["AUDIT_NETWORK_TEST_DATABASE_URL"]
    assert (urlparse(database).hostname in {"localhost", "127.0.0.1", "::1"}
            and urlparse(database).path.removeprefix("/").endswith("_test"))
    monkeypatch.setenv("AUDIT_NETWORK_DATA_DIR", str(tmp_path))
    tenant, actor = uuid4(), uuid4()
    with psycopg2.connect(database) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO iam.tenants(id,slug,name) VALUES(%s,%s,'Synthetic release test')",
                    (tenant, f"release-upload-{tenant.hex}"))
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (str(tenant),))
        cur.execute("INSERT INTO iam.principals(id,tenant_id,kind,subject,display_name) "
                    "VALUES(%s,%s,'human',%s,'Synthetic reviewer')", (actor, tenant, str(actor)))
    client = TestClient(create_app(Settings(database_url=database)))
    headers = {"X-Tenant-Id": str(tenant), "X-Trace-Id": str(uuid4()), "Idempotency-Key": uuid4().hex}

    def query(sql, params=()):
        with psycopg2.connect(database) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,true)", (str(tenant),))
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def upload(overrides=None, content=b"# synthetic audit evidence\n"):
        return client.post("/api/v1/knowledge/upload", headers={**headers, **(overrides or {})},
                           files={"files": ("audit.md", content, "text/markdown")})

    first = upload()
    assert first.status_code == 409, first.text
    tool = first.json()["detail"]["decision"]["tool_call_id"]
    approvals = client.get("/api/v1/approvals", headers=headers).json()["items"]
    approval = next(item for item in approvals if item["tool_call_id"] == tool)
    approved = client.post(f"/api/v1/approvals/{approval['id']}/decide",
                           headers={**headers, "X-Actor-Id": str(actor), "Idempotency-Key": uuid4().hex},
                           json={"decision": "approved", "reason": "Synthetic release acceptance"})
    assert approved.status_code == 200, approved.text
    yield upload, query, tenant, actor, approved.json()["authorization_lease_id"], tool
    query("UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s", (tenant,))


def test_approved_upload_continues_and_replays_one_batch(flow):
    upload, query, tenant, _, lease, tool = flow
    accepted, replay = upload(), upload()
    assert accepted.status_code == replay.status_code == 200, accepted.text
    assert accepted.json()["batch_id"] == replay.json()["batch_id"]
    assert query("SELECT count(*) FROM semantic.documents WHERE tenant_id=%s", (tenant,)) == [(1,)]
    assert query("SELECT consumed_at IS NOT NULL FROM control.authorization_leases WHERE id=%s", (lease,)) == [(True,)]
    assert {row[0] for row in query("SELECT decision FROM policy.decisions WHERE tool_call_id=%s", (tool,))} == {
        "REQUIRE_APPROVAL", "ALLOW"}


def test_approved_upload_cannot_transfer_to_new_key_or_body(flow):
    upload, _, _, _, _, _ = flow
    assert upload({"Idempotency-Key": uuid4().hex}).status_code == 409
    assert upload(content=b"changed evidence").status_code == 409
    assert upload().status_code == 200


@pytest.mark.parametrize("effect", ["deny", "freeze"])
def test_current_block_wins_over_approved_upload(flow, effect):
    upload, query, tenant, _, lease, _ = flow
    query("INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
          (tenant, f"release-{effect}", Json([{"effect": effect, "match": {"capabilities": ["knowledge.upload"]}}])))
    assert upload().status_code == 403
    assert query("SELECT consumed_at FROM control.authorization_leases WHERE id=%s", (lease,)) == [(None,)]


def test_expired_upload_lease_does_not_execute(flow):
    upload, query, _, _, lease, _ = flow
    query("UPDATE control.authorization_leases SET expires_at=now()-interval '1 second' WHERE id=%s", (lease,))
    assert upload().status_code == 409


def test_inactive_approver_cannot_authorize_upload(flow):
    upload, query, _, actor, _, _ = flow
    query("UPDATE iam.principals SET status='disabled' WHERE id=%s", (actor,))
    assert upload().status_code == 409


def test_managed_initialization_provisions_one_owner_without_reactivation(flow, tmp_path, monkeypatch):
    import json

    from packages.policy.maintenance import MaintenanceGateway

    _, query, tenant, _, _, _ = flow
    gateway = MaintenanceGateway(tmp_path, database_url=os.environ["AUDIT_NETWORK_TEST_DATABASE_URL"])
    monkeypatch.setattr(gateway, "_live_policy", lambda: (str(tenant), []))
    started = gateway.begin({"action": "initialize", "target": "0069_collaborative_planning",
                             "confirmed": True, "idempotency_key": uuid4().hex})
    gateway.finish(started["operation_id"], "succeeded")
    owner_path = tmp_path / "workspace-owner.json"
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    assert owner["tenant_id"] == str(tenant)
    assert query("SELECT status FROM iam.principals WHERE id=%s", (owner["actor_id"],)) == [("active",)]
    query("UPDATE iam.principals SET status='disabled' WHERE id=%s", (owner["actor_id"],))
    gateway.finish(started["operation_id"], "succeeded")
    assert query("SELECT status FROM iam.principals WHERE id=%s", (owner["actor_id"],)) == [("disabled",)]
    assert json.loads(owner_path.read_text(encoding="utf-8")) == owner
