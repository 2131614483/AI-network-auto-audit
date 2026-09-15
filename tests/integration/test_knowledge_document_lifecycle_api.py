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
            return row[0]


def _allow_lifecycle(tenant_id: UUID) -> None:
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
                (
                    tenant_id,
                    f"phase2-document-lifecycle-{uuid4().hex}",
                    Json(
                        [
                            {
                                "rule_id": str(uuid4()),
                                "effect": "allow",
                                "match": {
                                    "capabilities": [
                                        "knowledge.read",
                                        "knowledge.search",
                                        "knowledge.document.retire",
                                        "knowledge.recycle_bin.read",
                                        "knowledge.document.restore",
                                        "knowledge.upload",
                                    ]
                                },
                            }
                        ]
                    ),
                ),
            )


def test_document_retire_hides_searchable_content_and_restore_recovers_it(tmp_path: Path) -> None:
    """Delete is a tenant-scoped recycle-bin operation, never a hard delete."""

    source = tmp_path / f"lifecycle-{uuid4().hex}"
    source.mkdir()
    marker = f"文档回收站标记{uuid4().hex[:12]}"
    (source / "memo.md").write_text(marker, encoding="utf-8")
    ingested = ingest_directory(source, database_url=DB)
    assert ingested.batch_id
    tenant_id = _tenant_id()
    _allow_lifecycle(tenant_id)
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "SELECT id,current_version,content_sha256 FROM semantic.documents WHERE tenant_id=%s AND source_uri=%s",
                (tenant_id, ingested.files[0].source_uri),
            )
            document_id, version_before, hash_before = cur.fetchone()

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    adapters = client.get("/api/v1/knowledge/adapters", headers=headers)
    assert adapters.status_code == 200, adapters.text
    adapter_items = {item["key"]: item for item in adapters.json()["items"]}
    assert set(adapter_items) == {"local.mineru", "local.media"}
    # Audio/video transcription stays unconfigured regardless of MinerU presence.
    assert adapter_items["local.media"]["execution_mode"] == "not_configured"
    retired = client.post(
        f"/api/v1/knowledge/documents/{document_id}/retire",
        headers={**headers, "Idempotency-Key": f"retire-{uuid4().hex}"},
        json={"reason": "Phase 2 回收站验收"},
    )
    assert retired.status_code == 200, retired.text
    recycle_bin_id = retired.json()["recycle_bin_id"]

    documents = client.get("/api/v1/knowledge/documents", headers=headers)
    search = client.post(
        "/api/v1/knowledge/search",
        headers=headers,
        json={"query": marker, "mode": "keyword", "limit": 10},
    )
    recycle_bin = client.get("/api/v1/knowledge/recycle-bin", headers=headers)
    assert documents.status_code == search.status_code == recycle_bin.status_code == 200
    assert all(item["id"] != str(document_id) for item in documents.json()["items"])
    assert all(item["document_id"] != str(document_id) for item in search.json()["items"])
    assert any(item["id"] == recycle_bin_id and item["entity_id"] == str(document_id) for item in recycle_bin.json()["items"])

    restored = client.post(
        f"/api/v1/knowledge/recycle-bin/{recycle_bin_id}/restore",
        headers={**headers, "Idempotency-Key": f"restore-{uuid4().hex}"},
        json={"reason": "Phase 2 恢复验收"},
    )
    assert restored.status_code == 200, restored.text
    documents_after = client.get("/api/v1/knowledge/documents", headers=headers)
    search_after = client.post(
        "/api/v1/knowledge/search",
        headers=headers,
        json={"query": marker, "mode": "keyword", "limit": 10},
    )
    assert any(item["id"] == str(document_id) for item in documents_after.json()["items"])
    assert any(item["document_id"] == str(document_id) for item in search_after.json()["items"])

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "SELECT status,current_version,content_sha256 FROM semantic.documents WHERE tenant_id=%s AND id=%s",
                (tenant_id, document_id),
            )
            assert cur.fetchone() == ("staged", version_before, hash_before)
            cur.execute("SELECT restored_at IS NOT NULL FROM knowledge.recycle_bin WHERE tenant_id=%s AND id=%s", (tenant_id, recycle_bin_id))
            assert cur.fetchone() == (True,)
            cur.execute(
                """
                SELECT capability,arguments->>'document_id',arguments->>'recycle_bin_id',arguments->>'reason'
                FROM policy.tool_calls
                WHERE tenant_id=%s AND capability IN ('knowledge.document.retire','knowledge.document.restore')
                  AND arguments->>'reason' IN (%s,%s)
                """,
                (tenant_id, "Phase 2 回收站验收", "Phase 2 恢复验收"),
            )
            calls = cur.fetchall()
            assert {call[0] for call in calls} == {"knowledge.document.retire", "knowledge.document.restore"}
            assert any(call[1] == str(document_id) and call[3] == "Phase 2 回收站验收" for call in calls)
            assert any(call[2] == recycle_bin_id and call[3] == "Phase 2 恢复验收" for call in calls)


def test_retire_rejects_reused_idempotency_key_with_different_request(tmp_path: Path) -> None:
    """A replayed Idempotency-Key bound to a different document must fail closed (409)."""

    source = tmp_path / f"idem-{uuid4().hex}"
    source.mkdir()
    (source / "a.md").write_text("first document", encoding="utf-8")
    (source / "b.md").write_text("second document", encoding="utf-8")
    ingested = ingest_directory(source, database_url=DB)
    assert ingested.batch_id
    tenant_id = _tenant_id()
    _allow_lifecycle(tenant_id)
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "SELECT id FROM semantic.documents WHERE tenant_id=%s AND source_uri IN (%s,%s) ORDER BY source_uri",
                (tenant_id, ingested.files[0].source_uri, ingested.files[1].source_uri),
            )
            document_ids = [row[0] for row in cur.fetchall()]
    assert len(document_ids) == 2

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    key = f"retire-idem-{uuid4().hex}"
    reason = "幂等键复用验收"

    first = client.post(
        f"/api/v1/knowledge/documents/{document_ids[0]}/retire",
        headers={**headers, "Idempotency-Key": key},
        json={"reason": reason},
    )
    assert first.status_code == 200, first.text

    replay = client.post(
        f"/api/v1/knowledge/documents/{document_ids[1]}/retire",
        headers={**headers, "Idempotency-Key": key},
        json={"reason": reason},
    )
    assert replay.status_code == 409, replay.text
    assert "Idempotency-Key" in replay.text


def test_upload_rejects_reused_idempotency_key_with_different_files() -> None:
    """An upload bound to a different file set must not silently reuse the decision."""

    tenant_id = _tenant_id()
    _allow_lifecycle(tenant_id)
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4()), "Idempotency-Key": f"upload-idem-{uuid4().hex}"}

    first = client.post(
        "/api/v1/knowledge/upload",
        headers=headers,
        files=[("files", ("first.md", b"# first\n", "text/markdown"))],
    )
    assert first.status_code == 200, first.text

    replay = client.post(
        "/api/v1/knowledge/upload",
        headers=headers,
        files=[("files", ("second.md", b"# second\n", "text/markdown"))],
    )
    assert replay.status_code == 409, replay.text
    assert "Idempotency-Key" in replay.text


def test_upload_requires_an_idempotency_key() -> None:
    """An upload is a durable write and must not silently opt out of replay protection."""

    tenant_id = _tenant_id()
    _allow_lifecycle(tenant_id)
    client = TestClient(create_app(Settings(database_url=DB)))

    response = client.post(
        "/api/v1/knowledge/upload",
        headers={"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())},
        files=[("files", ("missing-key.md", b"# no idempotency key\n", "text/markdown"))],
    )

    assert response.status_code == 422, response.text
    assert "Idempotency-Key" in response.text


def test_upload_replay_returns_one_business_result_without_duplicate_documents() -> None:
    """Same key and bytes must replay the first result without reinvoking ingestion."""

    tenant_id = _tenant_id()
    _allow_lifecycle(tenant_id)
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {
        "X-Tenant-Id": str(tenant_id),
        "X-Trace-Id": str(uuid4()),
        "Idempotency-Key": f"upload-replay-{uuid4().hex}",
    }
    payload = [("files", ("memo.md", b"# deterministic upload\n", "text/markdown"))]
    form = {"relative_paths": "nested/memo.md"}
    replay_headers = {**headers, "X-Trace-Id": str(uuid4())}

    first = client.post("/api/v1/knowledge/upload", headers=headers, data=form, files=payload)
    replay = client.post("/api/v1/knowledge/upload", headers=replay_headers, data=form, files=payload)

    assert first.status_code == replay.status_code == 200
    first_body = first.json()
    replay_body = replay.json()
    assert replay_body["batch_id"] == first_body["batch_id"]
    assert replay_body["files"] == first_body["files"]
    assert replay_body["trace_id"] == replay_headers["X-Trace-Id"]
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "SELECT count(*) FROM semantic.documents WHERE tenant_id=%s AND source_uri=%s",
                (tenant_id, first_body["files"][0]["source_uri"]),
            )
            assert cur.fetchone() == (1,)
