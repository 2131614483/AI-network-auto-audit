from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.knowledge.ingest import ingest_directory

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            row = cur.fetchone()
            assert row is not None
            return UUID(str(row[0]))


def test_rich_media_extract_reaches_service_after_policy_allow() -> None:
    """The endpoint is wired through the durable gateway into the service layer."""

    tenant_id = _tenant_id()
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
                (
                    tenant_id,
                    f"phase3-rich-media-{uuid4().hex}",
                    Json(
                        [
                            {
                                "rule_id": str(uuid4()),
                                "effect": "allow",
                                "match": {
                                    "capabilities": ["knowledge.extract.rich_media"],
                                    "risk_classes": ["high"],
                                    "side_effects": ["write_data"],
                                },
                            }
                        ]
                    ),
                ),
            )
    client = TestClient(create_app(Settings(database_url=DB)))
    response = client.post(
        "/api/v1/knowledge/rich-media/extract",
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(uuid4()),
            "Idempotency-Key": f"rich-{uuid4().hex}",
        },
        json={"ingest_file_id": str(uuid4()), "method": "txt"},
    )
    # Policy allows, then the service rejects the unknown ingest file (422).
    assert response.status_code == 422, response.text
    assert "not pending local.mineru extraction" in response.text


def test_rich_media_extract_enqueues_without_running_the_worker(tmp_path: Path) -> None:
    """The API persists a policy-approved job; the Worker owns GPU execution."""

    tenant_id = _tenant_id()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute(
            """INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)""",
            (
                tenant_id,
                f"phase3-rich-media-queue-{uuid4().hex}",
                Json([{ "rule_id": str(uuid4()), "effect": "allow", "match": {
                    "capabilities": ["knowledge.extract.rich_media"], "risk_classes": ["high"], "side_effects": ["write_data"]
                }}]),
            ),
        )
    source = tmp_path / "api-queue.pdf"
    source.write_bytes(b"%PDF-1.4 API queue fixture")
    ingested = ingest_directory(tmp_path, database_url=DB)
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute(
            "SELECT id FROM knowledge.ingest_files WHERE tenant_id=%s AND batch_id=%s AND relative_path=%s",
            (tenant_id, ingested.batch_id, source.name),
        )
        ingest_file_id = UUID(str(cur.fetchone()[0]))

    key = f"rich-queue-{uuid4().hex}"
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4()), "Idempotency-Key": key}
    response = client.post(
        "/api/v1/knowledge/rich-media/extract",
        headers=headers,
        json={"ingest_file_id": str(ingest_file_id), "method": "txt"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ingest_file_id"] == str(ingest_file_id)
    assert body["status"] == "queued"
    replay = client.post(
        "/api/v1/knowledge/rich-media/extract",
        headers={**headers, "X-Trace-Id": str(uuid4())},
        json={"ingest_file_id": str(ingest_file_id), "method": "txt"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["job_id"] == body["job_id"]
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
        cur.execute("SELECT status FROM knowledge.ingest_files WHERE id=%s", (ingest_file_id,))
        assert cur.fetchone()[0] == "queued"
