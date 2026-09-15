from __future__ import annotations

import os

import psycopg2

from packages.graph.service import GraphBudget, GraphService

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def test_graph_lifecycle_and_bounded_query() -> None:
    service = GraphService(DB)
    service.ensure_space("phase4-test", "L1", "Phase 4 Test")
    source = service.upsert_node("phase4-test", "test:source", "test", "Source")
    target = service.upsert_node("phase4-test", "test:target", "test", "Target")
    service.add_alias(source, "Source Alias")
    service.upsert_edge("phase4-test", source, target, "links")
    result = service.bounded_neighbors("phase4-test", source, GraphBudget(max_nodes=2, max_edges=1, max_hops=2))
    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1
    service.soft_delete_node(source, {"canonical_key": "test:source", "label": "Source"})
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            tenant_id = cur.fetchone()[0]
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cur.execute("SELECT id FROM knowledge.recycle_bin WHERE entity_id=%s ORDER BY deleted_at DESC LIMIT 1", (source,))
            recycle_id = cur.fetchone()[0]
    restored = service.restore_node(recycle_id)
    assert restored == source
