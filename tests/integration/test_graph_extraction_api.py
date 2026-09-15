from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.knowledge.ingest import ingest_directory

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


def _publish_extraction_policy(tenant_id) -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                tenant_id,
                f"extract-api-{uuid4().hex[:8]}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["knowledge.extract.graph"], "risk_classes": ["read_only", "low"]}}]),
            ),
        )


def _publish_governance_policy(tenant_id) -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (
                tenant_id,
                f"extract-governance-{uuid4().hex[:8]}",
                Json([
                    {"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["graph.change.apply"], "risk_classes": ["medium"]}},
                    {"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": ["graph.change.reject"], "risk_classes": ["low"]}},
                ]),
            ),
        )


def test_extraction_preview_and_propose_are_policy_gated_and_shadow() -> None:
    temp_root = Path(__file__).resolve().parent.parent.parent / ".data" / f"extract-api-test-{uuid4().hex[:8]}"
    temp_root.mkdir(parents=True, exist_ok=True)
    (temp_root / "acme.md").write_text("ACME is regulated by the SEC and depends on Globex.", encoding="utf-8")
    ingest_directory(temp_root, database_url=DB, idempotency_key="extract-api-"+uuid4().hex)

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute("SELECT id FROM semantic.documents WHERE tenant_id=%s ORDER BY updated_at DESC LIMIT 1", (tenant_id,))
        doc_row = cur.fetchone()
        assert doc_row is not None
        document_id = doc_row[0]
    _publish_extraction_policy(tenant_id)

    from packages.graph.service import GraphService

    space = f"extract-api-space-{uuid4().hex[:10]}"
    GraphService(DB).ensure_space(space, "L1", "Extract API space")

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}

    # preview: read-only, no staging
    preview = client.post(
        "/api/v1/graph/extractions/preview",
        json={"document_id": str(document_id), "space_key": space, "limit_documents": 5},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()
    assert preview_payload["staged_as_changeset"] is None
    assert len(preview_payload["candidates"]) >= 1

    # propose: stages (or conflicts) a shadow ChangeSet, never applies
    propose = client.post(
        "/api/v1/graph/extractions/propose",
        json={"document_id": str(document_id), "space_key": space, "limit_documents": 5},
        headers={**headers, "Idempotency-Key": "extract-propose-"+uuid4().hex},
    )
    assert propose.status_code == 200, propose.text
    payload = propose.json()
    assert payload["candidates"] or payload["conflicts"]
    # nothing may appear in the active graph from a shadow proposal
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        for candidate in payload["candidates"]:
            cur.execute(
                "SELECT 1 FROM graph.nodes n JOIN graph.spaces s ON s.id=n.space_id "
                "WHERE n.tenant_id=%s AND s.key=%s AND n.canonical_key=%s",
                (tenant_id, space, candidate["node_key"]),
            )
            assert cur.fetchone() is None, f"proposal leaked active node: {candidate['node_key']}"


def test_proposal_governance_approve_applies_and_reject_stays_out() -> None:
    temp_root = Path(__file__).resolve().parent.parent.parent / ".data" / f"extract-governance-{uuid4().hex[:8]}"
    temp_root.mkdir(parents=True, exist_ok=True)
    (temp_root / "wonka.md").write_text("Wonka is regulated by the SEC and part of Umbrella.", encoding="utf-8")
    (temp_root / "domino.md").write_text("Domino is regulated by the FCA and depends on Wonka.", encoding="utf-8")
    ingest_directory(temp_root, database_url=DB, idempotency_key="extract-gov-"+uuid4().hex)

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute("SELECT id,title FROM semantic.documents WHERE tenant_id=%s AND title IN ('wonka','domino') ORDER BY title", (tenant_id,))
        docs = cur.fetchall()
    by_title = {title: doc_id for doc_id, title in docs}
    _publish_extraction_policy(tenant_id)
    _publish_governance_policy(tenant_id)

    from packages.graph.service import GraphService

    space = f"extract-gov-space-{uuid4().hex[:10]}"
    GraphService(DB).ensure_space(space, "L1", "Extract governance space")

    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}

    # stage two independent proposals on the same space, one per source document
    first = client.post(
        "/api/v1/graph/extractions/propose",
        json={"document_id": str(by_title["wonka"]), "space_key": space, "limit_documents": 5},
        headers={**headers, "Idempotency-Key": "extract-gov-propose-"+uuid4().hex},
    ).json()
    second = client.post(
        "/api/v1/graph/extractions/propose",
        json={"document_id": str(by_title["domino"]), "space_key": space, "limit_documents": 5},
        headers={**headers, "Idempotency-Key": "extract-gov-propose-"+uuid4().hex},
    ).json()
    assert first["staged_as_changeset"] and second["staged_as_changeset"]

    # list proposals
    listed = client.get("/api/v1/graph/extractions/proposals", headers=headers)
    assert listed.status_code == 200, listed.text
    proposals = listed.json()["proposals"]
    ids = {p["id"]: p for p in proposals}
    assert first["staged_as_changeset"] in ids
    assert second["staged_as_changeset"] in ids
    assert ids[first["staged_as_changeset"]]["candidate_count"] >= 1

    # approve the first (wonka) → its nodes appear in the live graph
    approved = client.post(
        f"/api/v1/graph/extractions/proposals/{first['staged_as_changeset']}/approve",
        headers={**headers, "Idempotency-Key": "extract-gov-approve-"+uuid4().hex},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "applied"
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        for node in ids[first["staged_as_changeset"]]["nodes"]:
            if node["operation"] != "create":
                continue
            cur.execute(
                "SELECT 1 FROM graph.nodes n JOIN graph.spaces s ON s.id=n.space_id "
                "WHERE n.tenant_id=%s AND s.key=%s AND n.canonical_key=%s AND n.deleted_at IS NULL",
                (tenant_id, space, node["node_key"]),
            )
            assert cur.fetchone() is not None, f"approved node missing: {node['node_key']}"

    # reject the second (domino) → its own nodes must not appear
    rejected = client.post(
        f"/api/v1/graph/extractions/proposals/{second['staged_as_changeset']}/reject",
        headers={**headers, "Idempotency-Key": "extract-gov-reject-"+uuid4().hex},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    listed_after = client.get("/api/v1/graph/extractions/proposals", headers=headers).json()["proposals"]
    by_id = {p["id"]: p for p in listed_after}
    assert by_id[second["staged_as_changeset"]]["status"] == "rejected"
    assert by_id[first["staged_as_changeset"]]["status"] == "applied"
    # domino-specific nodes were never released
    domino_keys = {n["node_key"] for n in ids[second["staged_as_changeset"]]["nodes"]}
    wonka_keys = {n["node_key"] for n in ids[first["staged_as_changeset"]]["nodes"]}
    only_domino = domino_keys - wonka_keys
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        for node_key in only_domino:
            cur.execute(
                "SELECT 1 FROM graph.nodes n JOIN graph.spaces s ON s.id=n.space_id "
                "WHERE n.tenant_id=%s AND s.key=%s AND n.canonical_key=%s AND n.deleted_at IS NULL",
                (tenant_id, space, node_key),
            )
            assert cur.fetchone() is None, f"rejected node leaked into graph: {node_key}"