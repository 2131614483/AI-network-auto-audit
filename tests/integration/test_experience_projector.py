"""Integration tests for ExperienceProjector against the native test database.

Proves the accumulation loop on real schema (RLS enforced):
* a finished run's attempt ledger yields observations + rollups;
* re-projecting the same run never double counts (idempotent);
* a full rebuild from observations equals the incremental rollups;
* failed/undeclared hand-offs are classified correctly.

Synthetic plugin ids are namespaced ``test.exp.*`` and removed in teardown via
the migrator connection (observation tables are append-only for audit_app).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg2
import pytest
from psycopg2.extras import Json

from packages.experience import ExperienceProjector, IllegalSuggestionTransition

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
MIGRATOR = os.getenv("AUDIT_NETWORK_MIGRATOR_DATABASE_URL", "postgresql://audit_migrator:admin@localhost:5432/audit_network_test")

PRODUCER = "test.exp.producer"
CONSUMER = "test.exp.consumer"
CONTRACT = "doc"


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


def _insert_attempt(cur, *, tenant: UUID, run_id: UUID, instance: str, plugin: str, seq: int,
                    status: str, bindings=None, started_ago_s: int = 4) -> None:
    now = datetime.now(timezone.utc)
    cur.execute(
        """INSERT INTO control.node_attempts
           (attempt_id,tenant_id,run_id,plan_key,execution_hash,node_instance_id,capability,
            plugin_id,attempt_seq,status,input_bindings,output_refs,trace_id,created_at,finished_at)
           VALUES(%s,%s,%s,'plan-exp','hash-exp',%s,%s,%s,%s,%s,%s,'{}',%s,%s,%s)""",
        (
            uuid4(), str(tenant), str(run_id), instance, f"cap.{plugin}", plugin, seq, status,
            Json(bindings or {}), f"trace-{run_id}",
            now - timedelta(seconds=started_ago_s), now - timedelta(seconds=started_ago_s - 2),
        ),
    )


def _cleanup(run_id: UUID) -> None:
    with psycopg2.connect(MIGRATOR) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant = str(cur.fetchone()[0])
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        cur.fetchone()
        cur.execute("DELETE FROM experience.edge_observations WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM experience.node_observations WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM control.node_attempts WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM experience.edge_stats WHERE source_plugin_id LIKE 'test.exp.%%'")
        cur.execute("DELETE FROM experience.node_stats WHERE plugin_id LIKE 'test.exp.%%'")
        cur.execute("DELETE FROM experience.relation_suggestions WHERE source_plugin_id LIKE 'test.exp.%%'")
        conn.commit()


@pytest.fixture()
def run_id():
    rid = uuid4()
    try:
        yield rid
    finally:
        _cleanup(rid)


def _edge_stat(cur, tenant: UUID):
    cur.execute(
        """SELECT success_count,fail_count,declared,confidence,weight,total_latency_ms,evidence_run_ids
           FROM experience.edge_stats
           WHERE tenant_id=%s AND source_plugin_id=%s AND target_plugin_id=%s AND contract_id=%s""",
        (str(tenant), PRODUCER, CONSUMER, CONTRACT),
    )
    return cur.fetchone()


def test_project_run_accumulates_and_is_idempotent(run_id) -> None:
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            _insert_attempt(cur, tenant=tenant, run_id=run_id, instance="n-a", plugin=PRODUCER, seq=1, status="succeeded")
            _insert_attempt(
                cur, tenant=tenant, run_id=run_id, instance="n-b", plugin=CONSUMER, seq=1, status="succeeded",
                bindings={"doc": {"source_instance": "n-a", "source_port": "doc", "sha256": "abc"}},
            )
        conn.commit()

    projector = ExperienceProjector(DB)
    declared = {(PRODUCER, CONSUMER, CONTRACT)}
    first = projector.project_run(tenant_id=tenant, run_id=run_id, trace_id=f"trace-{run_id}", declared_edges=declared)
    assert first["handovers"] == 1
    assert first["edge_observations_inserted"] == 1
    assert first["nodes_recomputed"] == 2

    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM experience.edge_observations WHERE tenant_id=%s AND run_id=%s",
                (str(tenant), str(run_id)),
            )
            assert cur.fetchone()[0] == 1
            stat = _edge_stat(cur, tenant)
    assert stat is not None
    success, fail, declared_flag, confidence, weight, latency, run_ids = stat
    assert (success, fail, declared_flag) == (1, 0, True)
    assert 0 < float(confidence) < 1  # Wilson conservative on a single sample
    assert float(weight) > 0
    assert latency >= 0
    assert str(run_id) in run_ids

    # Re-project: observation insert is a no-op and recompute keeps counts stable.
    second = projector.project_run(tenant_id=tenant, run_id=run_id, trace_id=f"trace-{run_id}", declared_edges=declared)
    assert second["edge_observations_inserted"] == 0
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            stat2 = _edge_stat(cur, tenant)
    assert stat2 is not None
    assert (stat2[0], stat2[1]) == (1, 0)


def test_rebuild_matches_incremental(run_id) -> None:
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            _insert_attempt(cur, tenant=tenant, run_id=run_id, instance="n-a", plugin=PRODUCER, seq=1, status="succeeded")
            _insert_attempt(
                cur, tenant=tenant, run_id=run_id, instance="n-b", plugin=CONSUMER, seq=1, status="succeeded",
                bindings={"doc": {"source_instance": "n-a"}},
            )
        conn.commit()

    projector = ExperienceProjector(DB)
    projector.project_run(tenant_id=tenant, run_id=run_id, trace_id="t", declared_edges={(PRODUCER, CONSUMER, CONTRACT)})
    before = projector.read_overlay(tenant_id=tenant)
    rebuilt = projector.rebuild(tenant_id=tenant)
    assert rebuilt["edges"] >= 1 and rebuilt["nodes"] >= 2
    after = projector.read_overlay(tenant_id=tenant)

    # Stored/time-free fields must be identical; decayed `weight` intentionally
    # drifts with read time and is excluded from the equality check.
    def stable(rows):
        return [{k: v for k, v in row.items() if k != "weight"} for row in rows]

    mine_before = [e for e in before["edge_stats"] if e["source"] == PRODUCER]
    mine_after = [e for e in after["edge_stats"] if e["source"] == PRODUCER]
    assert stable(mine_before) == stable(mine_after)
    nodes_before = stable([n for n in before["node_stats"] if n["plugin_id"] == PRODUCER])
    nodes_after = stable([n for n in after["node_stats"] if n["plugin_id"] == PRODUCER])
    assert nodes_before == nodes_after


def test_failed_undeclared_handover_classified(run_id) -> None:
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            _insert_attempt(cur, tenant=tenant, run_id=run_id, instance="n-a", plugin=PRODUCER, seq=1, status="succeeded")
            _insert_attempt(
                cur, tenant=tenant, run_id=run_id, instance="n-b", plugin=CONSUMER, seq=1, status="failed",
                bindings={"doc": {"source_instance": "n-a"}},
            )
        conn.commit()

    projector = ExperienceProjector(DB)
    projector.project_run(tenant_id=tenant, run_id=run_id, trace_id="t", declared_edges=set())  # undeclared
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            stat = _edge_stat(cur, tenant)
            cur.execute(
                "SELECT declared FROM experience.edge_observations WHERE run_id=%s",
                (str(run_id),),
            )
            obs_declared = cur.fetchone()[0]
    assert (stat[0], stat[1], stat[2]) == (0, 1, False)
    assert obs_declared is False


def _suggestion(cur, tenant: UUID):
    cur.execute(
        """SELECT suggestion_id,status,evidence_count,decided_by FROM experience.relation_suggestions
           WHERE tenant_id=%s AND source_plugin_id=%s AND target_plugin_id=%s AND contract_id=%s""",
        (str(tenant), PRODUCER, CONSUMER, CONTRACT),
    )
    return cur.fetchone()


def _seed_simple_run(tenant: UUID, run_id: UUID, consumer_status: str = "succeeded") -> None:
    with psycopg2.connect(DB) as conn:
        _tenant(conn)
        with conn.cursor() as cur:
            _insert_attempt(cur, tenant=tenant, run_id=run_id, instance="n-a", plugin=PRODUCER, seq=1, status="succeeded")
            _insert_attempt(
                cur, tenant=tenant, run_id=run_id, instance="n-b", plugin=CONSUMER, seq=1,
                status=consumer_status, bindings={"doc": {"source_instance": "n-a"}},
            )
        conn.commit()


def test_declared_edge_creates_no_suggestion(run_id) -> None:
    tenant = _local_tenant()
    _seed_simple_run(tenant, run_id)
    projector = ExperienceProjector(DB)
    summary = projector.project_run(
        tenant_id=tenant, run_id=run_id, trace_id="t",
        declared_edges={(PRODUCER, CONSUMER, CONTRACT)},
    )
    assert summary["suggestions_upserted"] == 0
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            assert _suggestion(cur, tenant) is None


def _local_tenant() -> UUID:
    with psycopg2.connect(DB) as conn:
        return _tenant(conn)


def test_proposed_suggestion_accept_then_frozen(run_id) -> None:
    tenant = _local_tenant()
    _seed_simple_run(tenant, run_id)
    projector = ExperienceProjector(DB)
    first = projector.project_run(tenant_id=tenant, run_id=run_id, trace_id="t", declared_edges=set())
    assert first["suggestions_upserted"] == 1

    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            sid, status, evidence, decided_by = _suggestion(cur, tenant)
    assert (status, evidence, decided_by) == ("proposed", 1, None)

    decided = projector.decide_suggestion(
        tenant_id=tenant, suggestion_id=sid, decision="accepted", decided_by="tester",
        trace_id="t-decide", idempotency_key=f"accept-{run_id}",
    )
    assert decided["status"] == "accepted"
    assert decided["decided_by"] == "tester"

    # Re-projecting the same run must not revive or overwrite a terminal row.
    projector.project_run(tenant_id=tenant, run_id=run_id, trace_id="t2", declared_edges=set())
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            _, status2, evidence2, decided2 = _suggestion(cur, tenant)
    assert (status2, evidence2, decided2) == ("accepted", 1, "tester")

    # accepted → dismissed is illegal
    with pytest.raises(IllegalSuggestionTransition):
        projector.decide_suggestion(
            tenant_id=tenant, suggestion_id=sid, decision="dismissed", decided_by="tester",
            trace_id="t-bad", idempotency_key="bad",
        )


def test_dismissed_suggestion_never_revives(run_id) -> None:
    tenant = _local_tenant()
    _seed_simple_run(tenant, run_id)
    projector = ExperienceProjector(DB)
    projector.project_run(tenant_id=tenant, run_id=run_id, trace_id="t", declared_edges=set())
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            sid = _suggestion(cur, tenant)[0]
    projector.decide_suggestion(
        tenant_id=tenant, suggestion_id=sid, decision="dismissed", decided_by="tester",
        trace_id="t", idempotency_key=f"dismiss-{run_id}",
    )
    projector.project_run(tenant_id=tenant, run_id=run_id, trace_id="t2", declared_edges=set())
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            assert _suggestion(cur, tenant)[1] == "dismissed"
