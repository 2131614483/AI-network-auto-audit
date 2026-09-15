"""Phase 6: governed graph merge / split and conflict arbitration.

All writes go through GraphService; a subset is exercised over the policy-gated
HTTP API to prove the merge/split/arbitration endpoints never bypass the gateway.
Optimistic-lock refusals land in the conflict inbox instead of silently
overwriting user data.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.graph.service import GraphService
from packages.plugin_runtime.registration import publish_graph_merge_arbitration_allow_policy

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


def _policy(connection, cur) -> tuple[UUID, str]:
    cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
    tenant_id = cur.fetchone()[0]
    cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
    cur.fetchone()
    return tenant_id, f"p6-{uuid4().hex[:10]}"


def _allow(cur, tenant_id: UUID, name: str, capabilities: list[str], risk_classes: list[str]) -> None:
    cur.execute(
        "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
        (tenant_id, name, Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": capabilities, "risk_classes": risk_classes}}])),
    )


def _current_revision(connection, tenant_id: UUID, node_id: UUID) -> int:
    with connection.cursor() as cur:
        cur.execute("SELECT row_version FROM graph.nodes WHERE tenant_id=%s AND id=%s", (tenant_id, node_id))
        return int(cur.fetchone()[0])


def _node_state(connection, tenant_id: UUID, node_id: UUID) -> tuple[str | None, int]:
    with connection.cursor() as cur:
        cur.execute("SELECT deleted_at,row_version FROM graph.nodes WHERE tenant_id=%s AND id=%s", (tenant_id, node_id))
        row = cur.fetchone()
        return row[0], int(row[1])


def test_merge_same_space_repoints_edges_and_soft_deletes_sources() -> None:
    service = GraphService(DB)
    suffix = uuid4().hex[:8]
    space = f"merge-{suffix}"
    service.ensure_space(space, "L3", "合并治理")
    tgt = service.upsert_node(space, f"company:{suffix}", "company", "ACME 母公司")
    src1 = service.upsert_node(space, f"company:{suffix}-dup", "company", "ACME 重复一")
    src2 = service.upsert_node(space, f"company:{suffix}-twin", "company", "ACME 重复二")
    neighbor = service.upsert_node(space, f"person:{suffix}", "person", "联系人")
    service.upsert_edge(space, src2, neighbor, "supported_by", 0.8)
    service.upsert_edge(space, neighbor, src1, "references", 0.6)

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id, _ = _policy(connection, cur)
        expected = _current_revision(connection, tenant_id, src1)
    result = service.merge_nodes(
        space, f"company:{suffix}", [f"company:{suffix}-dup", f"company:{suffix}-twin"],
        expected_revision=expected, reason="重复实体合并",
    )
    assert result["status"] == "applied"
    assert result["redirected_edges"] == 2
    assert result["source_keys"] == [f"company:{suffix}-dup", f"company:{suffix}-twin"]

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id, _ = _policy(connection, cur)
        cur.execute(
            """SELECT source_node_id,target_node_id FROM graph.edges
            WHERE tenant_id=%s AND space_id=(SELECT id FROM graph.spaces WHERE key=%s)
              AND deleted_at IS NULL AND valid_to IS NULL""",
            (tenant_id, space),
        )
        edges = {(str(s), str(t)) for s, t in cur.fetchall()}
        assert (str(src2), str(neighbor)) not in edges
        assert (str(neighbor), str(src1)) not in edges
        assert (str(tgt), str(neighbor)) in edges
        assert (str(neighbor), str(tgt)) in edges
        deleted_at, _ = _node_state(connection, tenant_id, src1)
        assert deleted_at is not None
        merge_id = UUID(result["merge_id"])
        cur.execute(
            "SELECT count(*) FROM graph.merge_edge_redirects WHERE tenant_id=%s AND merge_id=%s",
            (tenant_id, merge_id),
        )
        assert int(cur.fetchone()[0]) == 2
        cur.execute(
            "SELECT count(*) FROM graph.node_revisions WHERE tenant_id=%s AND node_id=ANY(%s) AND event_type='merged'",
            (tenant_id, [src1, src2]),
        )
        assert int(cur.fetchone()[0]) == 2
        cur.execute(
            "SELECT count(*) FROM graph.node_aliases WHERE tenant_id=%s AND node_id=%s AND alias_type='merge_source'",
            (tenant_id, tgt),
        )
        assert int(cur.fetchone()[0]) == 2


def test_split_repoints_only_declared_relations_and_keeps_source_active() -> None:
    service = GraphService(DB)
    suffix = uuid4().hex[:8]
    space = f"split-{suffix}"
    service.ensure_space(space, "L3", "拆分治理")
    source = service.upsert_node(space, f"domain:{suffix}", "domain", "聚合域")
    child_a = service.upsert_node(space, f"child-a:{suffix}", "entity", "子实体 A")
    child_b = service.upsert_node(space, f"child-b:{suffix}", "entity", "子实体 B")
    service.upsert_edge(space, source, child_a, "owns", 1.0)
    service.upsert_edge(space, child_b, source, "belongs_to", 1.0)
    service.upsert_edge(space, source, child_b, "mentions", 0.5)

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id, _ = _policy(connection, cur)
        expected = _current_revision(connection, tenant_id, source)
    result = service.split_node(
        space, f"domain:{suffix}",
        [
            {"node_key": f"domain:{suffix}-partA", "node_type": "partition", "label": "分片 A",
             "redirect_relations": ["owns"]},
        ],
        expected_revision=expected, reason="拆分职责",
    )
    assert result["status"] == "applied"
    part_key = result["parts"][0]

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id, _ = _policy(connection, cur)
        cur.execute(
            "SELECT id FROM graph.nodes WHERE tenant_id=%s AND canonical_key=%s AND space_id=(SELECT id FROM graph.spaces WHERE key=%s)",
            (tenant_id, part_key, space),
        )
        part_id = UUID(str(cur.fetchone()[0]))
        cur.execute(
            "SELECT source_node_id FROM graph.edges WHERE tenant_id=%s AND source_node_id=%s AND relation_type='owns' AND valid_to IS NULL",
            (tenant_id, part_id),
        )
        assert cur.fetchone() is not None
        cur.execute(
            "SELECT id FROM graph.edges WHERE tenant_id=%s AND source_node_id=%s AND relation_type='owns' AND valid_to IS NULL",
            (tenant_id, source),
        )
        assert cur.fetchone() is None
        # non-declared relations keep touching the source (one outgoing, one incoming)
        cur.execute(
            "SELECT count(*) FROM graph.edges WHERE tenant_id=%s AND (source_node_id=%s OR target_node_id=%s) AND relation_type IN ('belongs_to','mentions') AND valid_to IS NULL",
            (tenant_id, source, source),
        )
        assert int(cur.fetchone()[0]) == 2
        deleted_at, _ = _node_state(connection, tenant_id, source)
        assert deleted_at is None
        split_id = UUID(result["split_id"])
        cur.execute("SELECT count(*) FROM graph.split_parts WHERE tenant_id=%s AND split_id=%s", (tenant_id, split_id))
        assert int(cur.fetchone()[0]) == 1
        cur.execute("SELECT count(*) FROM graph.split_edge_redirects WHERE tenant_id=%s AND split_id=%s", (tenant_id, split_id))
        assert int(cur.fetchone()[0]) == 1


def test_merge_contract_rejections_and_optimistic_lock_creates_conflict() -> None:
    service = GraphService(DB)
    suffix = uuid4().hex[:8]
    space = f"contract-{suffix}"
    service.ensure_space(space, "L4", "契约拒绝")
    node = service.upsert_node(space, f"entity:{suffix}", "entity", "A")
    service.upsert_node(space, f"entity:{suffix}-b", "entity", "B")

    with pytest.raises(ValueError, match="cannot merge a node into itself"):
        service.merge_nodes(space, f"entity:{suffix}", [f"entity:{suffix}"], expected_revision=1, reason="self")
    with pytest.raises(ValueError, match="non-empty"):
        service.merge_nodes(space, f"entity:{suffix}", [], expected_revision=1, reason="empty")
    with pytest.raises(ValueError, match="namespaced"):
        service.merge_nodes(space, f"entity:{suffix}", ["not-a-key"], expected_revision=1, reason="bad")
    with pytest.raises(ValueError, match="active graph node not found"):
        service.merge_nodes(space, f"entity:{suffix}", [f"entity:{suffix}-ghost"], expected_revision=1, reason="missing")

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id, _ = _policy(connection, cur)
        expected = _current_revision(connection, tenant_id, node)
    with pytest.raises(ValueError, match="expected 99"):
        service.merge_nodes(space, f"entity:{suffix}", [f"entity:{suffix}-b"], expected_revision=99, reason="stale")

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id, _ = _policy(connection, cur)
        cur.execute(
            "SELECT status FROM knowledge.conflict_cases WHERE tenant_id=%s AND summary LIKE %s ORDER BY created_at DESC LIMIT 1",
            (tenant_id, "optimistic-lock merge%"),
        )
        assert cur.fetchone()[0] == "open"
        assert expected == _current_revision(connection, tenant_id, node)


def test_split_contract_rejections() -> None:
    service = GraphService(DB)
    suffix = uuid4().hex[:8]
    space = f"splitc-{suffix}"
    service.ensure_space(space, "L1", "拆分契约")
    service.upsert_node(space, f"role:{suffix}", "role", "角色")
    service.upsert_node(space, f"role:{suffix}-exists", "role", "已存在")

    with pytest.raises(ValueError, match="requires at least one part"):
        service.split_node(space, f"role:{suffix}", [], expected_revision=1, reason="none")
    with pytest.raises(ValueError, match="cannot equal the source"):
        service.split_node(space, f"role:{suffix}", [{"node_key": f"role:{suffix}", "node_type": "role", "label": "x"}], expected_revision=1, reason="self")
    with pytest.raises(ValueError, match="part node_key must be a namespaced"):
        service.split_node(space, f"role:{suffix}", [{"node_key": "bad", "node_type": "role", "label": "x"}], expected_revision=1, reason="bad")
    with pytest.raises(ValueError, match="duplicate part node_key"):
        service.split_node(space, f"role:{suffix}", [{"node_key": f"role:{suffix}-a", "node_type": "role", "label": "a"}, {"node_key": f"role:{suffix}-a", "node_type": "role", "label": "b"}], expected_revision=1, reason="dup")
    with pytest.raises(ValueError, match="already exists"):
        service.split_node(space, f"role:{suffix}", [{"node_key": f"role:{suffix}-exists", "node_type": "role", "label": "x"}], expected_revision=1, reason="exists")


def test_arbitration_is_explicit_once_only_and_merge_decision_links_a_merge() -> None:
    service = GraphService(DB)
    suffix = uuid4().hex[:8]
    space = f"arb-{suffix}"
    service.ensure_space(space, "L4", "仲裁")
    service.upsert_node(space, f"app:{suffix}", "app", "应用")
    source = service.upsert_node(space, f"app:{suffix}-dup", "app", "应用副本")
    case_id = service.record_conflict(
        f"entity:{suffix}", "重复实体主张", "file:///audit/dup.txt",
        {"node_key": f"app:{suffix}-dup", "claim": "aligned"},
    )
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id, _ = _policy(connection, cur)
        expected = _current_revision(connection, tenant_id, source)
        cur.execute("SELECT status FROM knowledge.conflict_cases WHERE tenant_id=%s AND id=%s", (tenant_id, case_id))
        assert cur.fetchone()[0] == "open"

    decision = service.arbitrate_conflict(
        case_id, "merge", "仲裁:重复合并",
        merge={"space_key": space, "target_key": f"app:{suffix}", "source_keys": [f"app:{suffix}-dup"], "expected_revision": expected},
    )
    assert decision["decision"] == "merge"
    assert decision["status"] == "resolved"
    assert decision["resolution_merge_id"]
    with pytest.raises(ValueError, match="already arbitrated|not open"):
        service.arbitrate_conflict(case_id, "keep_source", "重复裁决")

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id, _ = _policy(connection, cur)
        cur.execute("SELECT decision FROM knowledge.arbitration_decisions WHERE tenant_id=%s AND case_id=%s", (tenant_id, case_id))
        assert cur.fetchone()[0] == "merge"
        cur.execute("SELECT status FROM knowledge.conflict_cases WHERE tenant_id=%s AND id=%s", (tenant_id, case_id))
        assert cur.fetchone()[0] == "resolved"

    second_case = service.record_conflict(
        f"entity:{suffix}", "纯标记主张", "file:///audit/keep.txt",
        {"claim": "keep-source"},
    )
    second = service.arbitrate_conflict(second_case, "reject_claim", "驳回该主张")
    assert second["status"] == "resolved"
    assert second["resolution_merge_id"] is None


def test_publish_policy_and_gated_api_endpoints() -> None:
    service = GraphService(DB)
    suffix = uuid4().hex[:8]
    space = f"api-p6-{suffix}"
    service.ensure_space(space, "L3", "API 合并")
    service.upsert_node(space, f"api-co:{suffix}", "company", "目标")
    source = service.upsert_node(space, f"api-co:{suffix}-d", "company", "副本")

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id, name = _policy(connection, cur)
        cur.execute("SELECT row_version FROM graph.nodes WHERE tenant_id=%s AND id=%s", (tenant_id, source))
        row = cur.fetchone()
        assert row is not None
        expected = int(row[0])
        # purge any Phase 6 allow sets left behind by earlier runs on the shared
        # test database so the negative gate below is deterministic (UPDATE, not
        # DELETE: the app role is granted SELECT/INSERT/UPDATE on policy_sets)
        cur.execute(
            """UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s
            AND (rules::text LIKE '%%graph.node.merge%%'
                 OR rules::text LIKE '%%graph.node.split%%'
                 OR rules::text LIKE '%%graph.arbitration.resolve%%')""",
            (tenant_id,),
        )
        # no policy yet -> merge is refused by the gateway
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4()), "Idempotency-Key": str(uuid4())}
    denied = client.post(
        "/api/v1/graph/nodes/merge",
        json={"space_key": space, "target_key": f"api-co:{suffix}", "source_keys": [f"api-co:{suffix}-d"], "expected_revision": expected, "reason": "合并"},
        headers=headers,
    )
    assert denied.status_code in (403, 409)

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id, _ = _policy(connection, cur)
        _allow(cur, tenant_id, name, ["graph.node.merge", "graph.node.split", "graph.arbitration.resolve"], ["medium"])
        _allow(cur, tenant_id, f"{name}-read", ["graph.governance.read"], ["read_only"])

    headers["Idempotency-Key"] = str(uuid4())
    ok = client.post(
        "/api/v1/graph/nodes/merge",
        json={"space_key": space, "target_key": f"api-co:{suffix}", "source_keys": [f"api-co:{suffix}-d"], "expected_revision": expected, "reason": "合并"},
        headers=headers,
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "applied"
    assert body["merge_id"]

    records = client.get("/api/v1/graph/merges", params={"space_key": space, "limit": 5}, headers={"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())})
    assert records.status_code == 200
    assert any(item["target_node_key"] == f"api-co:{suffix}" for item in records.json()["items"])


def test_publish_policy_registers_phase6_capabilities() -> None:
    tenant_id = publish_graph_merge_arbitration_allow_policy(DB)
    assert UUID(str(tenant_id))
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        from packages.plugin_runtime.registration import _PHASE6_POLICY_NAME

        cur.execute("SELECT rules FROM policy.policy_sets WHERE tenant_id=%s AND name=%s AND status='active'", (tenant_id, _PHASE6_POLICY_NAME))
        rules = cur.fetchone()[0]
        caps = {r["match"]["capabilities"][0] for r in rules}
        assert {"graph.node.merge", "graph.node.split", "graph.arbitration.resolve"} == caps