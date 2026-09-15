"""Integration tests for L2 light-solidification (design §5.5 exit 1).

An accepted design-external relation is recorded as a governance ChangeSet of
kind ``experience.relation`` (change_type='experience'), published through the
existing knowledge release lifecycle, then linked back onto the suggestion as a
*released/confirmed* relation.  No graph.node row and no plugin manifest is
written.  Solidification is idempotent.

Synthetic ``test.exp.*`` rows are removed in teardown via the migrator connection
(governance/evidence tables are append-only for the app role).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg2
import pytest
from psycopg2.extras import Json

from packages.experience import ExperienceProjector
from packages.experience.solidify import SolidificationError, solidify_accepted_suggestion
from packages.knowledge.lifecycle import ChangeOperation, KnowledgeLifecycleService

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
MIGRATOR = os.getenv("AUDIT_NETWORK_MIGRATOR_DATABASE_URL", "postgresql://audit_migrator:admin@localhost:5432/audit_network_test")

PRODUCER = "test.exp.solid.producer"
CONSUMER = "test.exp.solid.consumer"
CONTRACT = "doc"
RELATION_KIND = "experience.relation"


def _tenant(conn) -> UUID:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        if row is None:
            pytest.skip("local-dev tenant missing")
        tenant = UUID(str(row[0]))
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.fetchone()
    return tenant


def _local_tenant() -> UUID:
    with psycopg2.connect(DB) as conn:
        return _tenant(conn)


def _insert_attempt(cur, *, tenant: UUID, run_id: UUID, instance: str, plugin: str,
                    status: str, bindings=None) -> None:
    now = datetime.now(timezone.utc)
    cur.execute(
        """INSERT INTO control.node_attempts
           (attempt_id,tenant_id,run_id,plan_key,execution_hash,node_instance_id,capability,
            plugin_id,attempt_seq,status,input_bindings,output_refs,trace_id,created_at,finished_at)
           VALUES(%s,%s,%s,'plan-exp','h',%s,%s,%s,1,%s,%s,'{}',%s,%s,%s)""",
        (uuid4(), str(tenant), str(run_id), instance, f"cap.{plugin}", plugin, status,
         Json(bindings or {}), f"trace-{run_id}", now - timedelta(seconds=4), now - timedelta(seconds=2)),
    )


def _seed_accepted_suggestion(tenant: UUID, run_id: UUID) -> UUID:
    """Project an undeclared hand-off → proposed, then accept it; return its id."""
    with psycopg2.connect(DB) as conn:
        _tenant(conn)
        with conn.cursor() as cur:
            _insert_attempt(cur, tenant=tenant, run_id=run_id, instance="n-a", plugin=PRODUCER, status="succeeded")
            _insert_attempt(cur, tenant=tenant, run_id=run_id, instance="n-b", plugin=CONSUMER,
                            status="succeeded", bindings={"doc": {"source_instance": "n-a"}})
        conn.commit()
    projector = ExperienceProjector(DB)
    projector.project_run(tenant_id=tenant, run_id=run_id, trace_id="t", declared_edges=set())
    with psycopg2.connect(DB) as conn:
        _tenant(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT suggestion_id FROM experience.relation_suggestions
                   WHERE tenant_id=%s AND source_plugin_id=%s AND target_plugin_id=%s AND contract_id=%s""",
                (str(tenant), PRODUCER, CONSUMER, CONTRACT),
            )
            sid = UUID(str(cur.fetchone()[0]))
    projector.decide_suggestion(tenant_id=tenant, suggestion_id=sid, decision="accepted",
                                decided_by="tester", trace_id="t-dec", idempotency_key=f"acc-{run_id}")
    return sid


def _cleanup(run_id: UUID) -> None:
    with psycopg2.connect(MIGRATOR) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant = str(cur.fetchone()[0])
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        cur.fetchone()
        # Governance FK order: detach suggestion links, drop self-referential
        # release parents, then releases -> validation_runs -> operations -> sets.
        cur.execute("SELECT id FROM knowledge.change_sets WHERE title LIKE '%%test.exp.solid.%%'")
        cs_ids = [str(r[0]) for r in cur.fetchall()]
        cur.execute(
            "UPDATE experience.relation_suggestions SET release_id=NULL, changeset_id=NULL "
            "WHERE source_plugin_id LIKE 'test.exp.solid.%%'"
        )
        if cs_ids:
            cur.execute("SELECT id FROM knowledge.releases WHERE changeset_id = ANY(%s::uuid[])", (cs_ids,))
            rel_ids = [str(r[0]) for r in cur.fetchall()]
            if rel_ids:
                cur.execute("UPDATE knowledge.releases SET parent_release_id=NULL WHERE parent_release_id = ANY(%s::uuid[])", (rel_ids,))
                cur.execute("DELETE FROM knowledge.releases WHERE id = ANY(%s::uuid[])", (rel_ids,))
            cur.execute("DELETE FROM knowledge.validation_runs WHERE change_set_id = ANY(%s::uuid[])", (cs_ids,))
        cur.execute("DELETE FROM knowledge.change_operations WHERE payload->>'source_plugin_id' LIKE 'test.exp.solid.%%'")
        if cs_ids:
            cur.execute("DELETE FROM knowledge.change_sets WHERE id = ANY(%s::uuid[])", (cs_ids,))
        cur.execute("DELETE FROM experience.edge_observations WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM experience.node_observations WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM control.node_attempts WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM experience.edge_stats WHERE source_plugin_id LIKE 'test.exp.solid.%%'")
        cur.execute("DELETE FROM experience.node_stats WHERE plugin_id LIKE 'test.exp.solid.%%'")
        cur.execute("DELETE FROM experience.relation_suggestions WHERE source_plugin_id LIKE 'test.exp.solid.%%'")
        conn.commit()


@pytest.fixture()
def run_id():
    rid = uuid4()
    try:
        yield rid
    finally:
        _cleanup(rid)


def _relation_op(suggestion_id: UUID) -> ChangeOperation:
    return ChangeOperation(
        RELATION_KIND, "create",
        {
            "suggestion_id": str(suggestion_id),
            "source_plugin_id": PRODUCER,
            "target_plugin_id": CONSUMER,
            "contract_id": CONTRACT,
            "evidence_run_ids": ["run-1"],
            "decided_by": "tester",
            "confidence": 0.4,
            "weight": 1.1,
        },
        target_id=suggestion_id,
    )


def _insert_accepted_suggestion(tenant: UUID) -> UUID:
    sid = uuid4()
    with psycopg2.connect(DB) as conn:
        _tenant(conn)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO experience.relation_suggestions
                   (suggestion_id,tenant_id,source_plugin_id,target_plugin_id,contract_id,status,
                    evidence_count,proposed_by,decided_by,decided_at,idempotency_key)
                   VALUES(%s,%s,%s,%s,%s,'accepted',1,'system','tester',now(),%s)""",
                (str(sid), str(tenant), PRODUCER, CONSUMER, CONTRACT, f"seed-{sid}"),
            )
        conn.commit()
    return sid


def test_lifecycle_experience_relation_activates_without_graph_nodes(run_id) -> None:
    tenant = _local_tenant()
    sid = _insert_accepted_suggestion(tenant)
    lifecycle = KnowledgeLifecycleService(DB)
    with psycopg2.connect(DB) as conn:
        _tenant(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM graph.nodes WHERE tenant_id=%s", (str(tenant),))
            nodes_before = cur.fetchone()[0]

    changeset = lifecycle.create_changeset("经验关系固化 test.exp", "integration", [_relation_op(sid)])
    result = lifecycle.validate_changeset(changeset)
    assert result.passed is True
    lifecycle.approve_changeset(changeset)
    release = lifecycle.create_release(changeset, f"exp-rel-{run_id}")
    lifecycle.activate_release(release)

    with psycopg2.connect(DB) as conn:
        _tenant(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM graph.nodes WHERE tenant_id=%s", (str(tenant),))
            assert cur.fetchone()[0] == nodes_before  # no node materialised
            cur.execute("SELECT change_type,status FROM knowledge.change_sets WHERE id=%s", (changeset,))
            assert cur.fetchone() == ("experience", "applied")
            cur.execute("SELECT status FROM knowledge.releases WHERE id=%s", (release,))
            assert cur.fetchone()[0] == "active"
            cur.execute("SELECT status,target_kind FROM knowledge.change_operations WHERE change_set_id=%s", (changeset,))
            assert cur.fetchone() == ("applied", RELATION_KIND)


def test_lifecycle_rejects_mixed_and_non_create_relation(run_id) -> None:
    tenant = _local_tenant()
    sid = _insert_accepted_suggestion(tenant)
    lifecycle = KnowledgeLifecycleService(DB)
    mixed = [_relation_op(sid), ChangeOperation("graph.node", "create", {"label": "x"})]
    with pytest.raises(ValueError, match="same target kind"):
        lifecycle.create_changeset("mixed", "t", mixed)
    bad_retire = ChangeOperation(RELATION_KIND, "retire", {"source_plugin_id": PRODUCER}, target_id=sid)
    with pytest.raises(ValueError, match="only support"):
        lifecycle.create_changeset("bad", "t", [bad_retire])


def test_lifecycle_relation_validation_requires_core_payload(run_id) -> None:
    tenant = _local_tenant()
    sid = _insert_accepted_suggestion(tenant)
    lifecycle = KnowledgeLifecycleService(DB)
    op = ChangeOperation(RELATION_KIND, "create", {"suggestion_id": str(sid)}, target_id=sid)
    changeset = lifecycle.create_changeset("missing fields", "t", [op])
    result = lifecycle.validate_changeset(changeset)
    assert result.passed is False
    assert any("source_plugin_id" in v for v in result.violations)


def test_solidify_accepted_publishes_and_links_back(run_id) -> None:
    tenant = _local_tenant()
    sid = _seed_accepted_suggestion(tenant, run_id)
    outcome = solidify_accepted_suggestion(DB, tenant_id=tenant, suggestion_id=sid)
    assert outcome["already_released"] is False
    assert outcome["release_id"] and outcome["changeset_id"]

    with psycopg2.connect(DB) as conn:
        _tenant(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status,changeset_id,release_id,released_at FROM experience.relation_suggestions WHERE suggestion_id=%s",
                (str(sid),),
            )
            status, cs_id, rel_id, released_at = cur.fetchone()
    assert status == "accepted"
    assert released_at is not None
    assert str(cs_id) == str(outcome["changeset_id"])
    assert str(rel_id) == str(outcome["release_id"])


def test_solidify_is_idempotent(run_id) -> None:
    tenant = _local_tenant()
    sid = _seed_accepted_suggestion(tenant, run_id)
    first = solidify_accepted_suggestion(DB, tenant_id=tenant, suggestion_id=sid)
    second = solidify_accepted_suggestion(DB, tenant_id=tenant, suggestion_id=sid)
    assert second["already_released"] is True
    assert str(second["release_id"]) == str(first["release_id"])
    with psycopg2.connect(DB) as conn:
        _tenant(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM knowledge.change_operations WHERE payload->>'suggestion_id'=%s",
                (str(sid),),
            )
            assert cur.fetchone()[0] == 1  # no duplicate governance record


def test_solidify_rejects_non_accepted(run_id) -> None:
    tenant = _local_tenant()
    # project a proposed suggestion but do NOT accept it
    with psycopg2.connect(DB) as conn:
        _tenant(conn)
        with conn.cursor() as cur:
            _insert_attempt(cur, tenant=tenant, run_id=run_id, instance="n-a", plugin=PRODUCER, status="succeeded")
            _insert_attempt(cur, tenant=tenant, run_id=run_id, instance="n-b", plugin=CONSUMER,
                            status="succeeded", bindings={"doc": {"source_instance": "n-a"}})
        conn.commit()
    projector = ExperienceProjector(DB)
    projector.project_run(tenant_id=tenant, run_id=run_id, trace_id="t", declared_edges=set())
    with psycopg2.connect(DB) as conn:
        _tenant(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT suggestion_id FROM experience.relation_suggestions WHERE source_plugin_id=%s",
                (PRODUCER,),
            )
            sid = UUID(str(cur.fetchone()[0]))
    with pytest.raises(SolidificationError, match="accepted"):
        solidify_accepted_suggestion(DB, tenant_id=tenant, suggestion_id=sid)
