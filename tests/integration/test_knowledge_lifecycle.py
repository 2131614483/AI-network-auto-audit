from __future__ import annotations

import os
import uuid

import psycopg2
import pytest

from packages.graph.service import GraphService
from packages.knowledge.lifecycle import ChangeOperation, KnowledgeLifecycleService

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


@pytest.fixture()
def database() -> None:
    try:
        with psycopg2.connect(DB):
            return
    except psycopg2.Error as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"native PostgreSQL unavailable: {exc}")


def test_changeset_release_activation_and_compensating_rollback(database: None) -> None:
    suffix = uuid.uuid4().hex[:10]
    graph = GraphService(DB)
    lifecycle = KnowledgeLifecycleService(DB)
    space = f"lifecycle-{suffix}"
    graph.ensure_space(space, "L1", "Lifecycle test")
    changeset = lifecycle.create_changeset(
        "Create lifecycle node",
        "integration test",
        [
            ChangeOperation(
                "graph.node",
                "create",
                {"space_key": space, "canonical_key": f"lifecycle:{suffix}", "node_type": "test", "label": "v1", "properties": {"source": "test"}},
            )
        ],
        graph_space_key=space,
    )
    validation = lifecycle.validate_changeset(changeset)
    assert validation.passed is True
    lifecycle.approve_changeset(changeset)
    release = lifecycle.create_release(changeset, f"release-{suffix}")
    lifecycle.activate_release(release)

    rollback = lifecycle.rollback_release(release, f"rollback-{suffix}")
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute("SELECT deleted_at FROM graph.nodes WHERE tenant_id=%s AND canonical_key=%s", (tenant_id, f"lifecycle:{suffix}"))
        assert cur.fetchone()[0] is not None
        cur.execute("SELECT status FROM knowledge.releases WHERE id=%s", (release,))
        assert cur.fetchone()[0] == "inactive"
        cur.execute("SELECT status FROM knowledge.releases WHERE id=%s", (rollback,))
        assert cur.fetchone()[0] == "active"
