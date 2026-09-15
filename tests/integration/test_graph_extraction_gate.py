from __future__ import annotations

import os
import uuid

import psycopg2
import pytest

from packages.graph.service import GraphService
from packages.knowledge.graph_extraction import (
    GraphExtractionCandidate,
    GraphExtractionService,
)

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


@pytest.fixture()
def database() -> None:
    try:
        with psycopg2.connect(DB):
            return
    except psycopg2.Error as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"native PostgreSQL unavailable: {exc}")


def _node_exists(graph: GraphService, space: str, node_key: str) -> tuple[bool, object]:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant = cur.fetchone()
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant[0]),))
        cur.execute(
            "SELECT n.deleted_at FROM graph.nodes n JOIN graph.spaces s ON s.id=n.space_id "
            "WHERE s.tenant_id=%s AND s.key=%s AND n.canonical_key=%s AND n.tenant_id=%s",
            (tenant[0], space, node_key, tenant[0]),
        )
        found = cur.fetchone()
    if found is None:
        return False, None
    return True, found[0]


def _candidate(node_key: str, space_key: str, source_uri: str) -> GraphExtractionCandidate:
    return GraphExtractionCandidate(
        document_id=uuid.uuid4(),
        source_uri=source_uri,
        space_key=space_key,
        node_key=node_key,
        node_type="company",
        label="ACME",
        property_source={"title": "t", "source_uri": source_uri},
        confidence=0.9,
    )


def test_shadow_changeset_gate_not_applied_without_approval(database: None) -> None:
    suffix = uuid.uuid4().hex[:10]
    from packages.knowledge.lifecycle import KnowledgeLifecycleService

    graph_svc = GraphService(DB)
    lifecycle_svc = KnowledgeLifecycleService(DB)
    extraction = GraphExtractionService(DB)
    space = f"extract-{suffix}"
    graph_svc.ensure_space(space, "L1", "Extract gate test")
    candidates = [_candidate(f"company:{suffix}", space, f"drop://{suffix}/a.md")]
    staged = extraction.route_candidates(candidates, allow_spaces={space})
    assert staged.candidates, "expected a stageable candidate"
    changeset = extraction.stage_proposal(
        staged.candidates, f"Extract {suffix}", "gate test", graph_space_key=space
    )

    # pending review -> NOT yet in the active graph
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        t = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(t),))
        cur.execute("SELECT status FROM knowledge.change_sets WHERE id=%s", (changeset,))
        assert cur.fetchone()[0] in {"draft", "pending_review"}  # shadow/staged, not applied
    present, _ = _node_exists(graph_svc, space, f"company:{suffix}")
    assert present is False, "unapproved node leaked into graph"

    # validate -> approve -> release -> activate
    validation = lifecycle_svc.validate_changeset(changeset)
    assert validation.passed is True
    lifecycle_svc.approve_changeset(changeset)
    release = lifecycle_svc.create_release(changeset, f"release-{suffix}")
    lifecycle_svc.activate_release(release)

    present, deleted_at = _node_exists(graph_svc, space, f"company:{suffix}")
    assert present is True, "approved node missing from graph"
    assert deleted_at is None


def test_route_candidates_rejects_unregistered_space_via_conflict(database: None) -> None:
    service = GraphExtractionService(DB)
    candidate = _candidate("company:acme", "not-registered-space", "drop://c.md")
    result = service.route_candidates([candidate], allow_spaces={"audit-l1"})
    assert not result.candidates
    assert result.conflicts
    assert any("unregistered space" in c["summary"] for c in result.conflicts)


def test_route_candidates_conflicts_on_existing_active_node(database: None) -> None:
    suffix = uuid.uuid4().hex[:10]
    graph = GraphService(DB)
    space = f"extract-exist-{suffix}"
    graph.ensure_space(space, "L1", "Extract existing test")
    graph.upsert_node(space, "company:acme", "company", "ACME")
    service = GraphExtractionService(DB)
    result = service.route_candidates([_candidate("company:acme", space, "drop://a.md")], allow_spaces={space})
    assert not result.candidates
    assert any("already exists" in c["summary"] for c in result.conflicts)