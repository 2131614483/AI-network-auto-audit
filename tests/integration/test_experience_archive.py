"""The project anchor: which business project a run — and its plugins — served.

``experience.node_observations`` already recorded *what* ran.  ``archive_links``
records *for whom*, so the graph can answer "which projects has this plugin
version been proven in?".

Everything here is namespaced ``test.exp.*`` / ``test-archive-*`` and removed in
teardown through the migrator connection: ``archive_links`` and the observation
tables are append-only for the app role by design (evidence is never rewritten),
so the app role cannot clean up after itself.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg2
import pytest
from psycopg2.extras import Json

from packages.experience import ExperienceProjector
from packages.experience.projector import project_run_best_effort

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
MIGRATOR = os.getenv("AUDIT_NETWORK_MIGRATOR_DATABASE_URL", "postgresql://audit_migrator:admin@localhost:5432/audit_network_test")

PLUGIN = "test.exp.archived"
PROJECT_SLUG = "test-archive-project"


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


def _project(cur, tenant: UUID) -> UUID:
    cur.execute(
        """INSERT INTO iam.projects(tenant_id,slug,name) VALUES(%s,%s,'归档测试项目')
           ON CONFLICT (tenant_id,slug) DO UPDATE SET name=EXCLUDED.name RETURNING id""",
        (str(tenant), PROJECT_SLUG),
    )
    return UUID(str(cur.fetchone()[0]))


def _insert_attempt(cur, *, tenant: UUID, run_id: UUID, plugin_version: str) -> None:
    now = datetime.now(timezone.utc)
    cur.execute(
        """INSERT INTO control.node_attempts
           (attempt_id,tenant_id,run_id,plan_key,execution_hash,node_instance_id,capability,
            plugin_id,plugin_version,runtime_code_sha256,attempt_seq,status,input_bindings,output_refs,
            trace_id,created_at,finished_at)
           VALUES(%s,%s,%s,'plan-archive','hash-archive',%s,%s,%s,%s,%s,1,'succeeded','{}','{}',%s,%s,%s)""",
        (
            uuid4(), str(tenant), str(run_id), "node-a", f"cap.{PLUGIN}", PLUGIN,
            plugin_version, "d" * 64, f"trace-{run_id}",
            now - timedelta(seconds=4), now - timedelta(seconds=2),
        ),
    )


def _cleanup(run_id: UUID) -> None:
    with psycopg2.connect(MIGRATOR) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant = str(cur.fetchone()[0])
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        cur.fetchone()
        cur.execute("DELETE FROM experience.archive_links WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM experience.node_observations WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM experience.node_stats WHERE plugin_id=%s", (PLUGIN,))
        cur.execute("DELETE FROM experience.relation_suggestions WHERE source_plugin_id=%s", (PLUGIN,))
        cur.execute("DELETE FROM control.node_attempts WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM iam.projects WHERE slug=%s AND tenant_id=%s", (PROJECT_SLUG, tenant))
        conn.commit()


@pytest.fixture()
def anchored_run():
    run_id = uuid4()
    try:
        yield run_id
    finally:
        _cleanup(run_id)


def test_plugin_version_flows_from_the_attempt_into_the_observation(anchored_run) -> None:
    """The follow-through of 0060: the experience layer keeps the version."""
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            _insert_attempt(cur, tenant=tenant, run_id=anchored_run, plugin_version="0.4.2")
        conn.commit()

    ExperienceProjector(DB).project_run(
        tenant_id=tenant, run_id=anchored_run, trace_id=f"trace-{anchored_run}", declared_edges=set()
    )
    with psycopg2.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.fetchone()
        cur.execute(
            "SELECT plugin_version FROM experience.node_observations WHERE run_id=%s",
            (str(anchored_run),),
        )
        assert [r[0] for r in cur.fetchall()] == ["0.4.2"]


def test_archive_run_is_idempotent(anchored_run) -> None:
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            project_id = _project(cur, tenant)
        conn.commit()

    projector = ExperienceProjector(DB)
    first = projector.archive_run(
        tenant_id=tenant, run_id=anchored_run, project_id=project_id, trace_id="t", note="首轮"
    )
    second = projector.archive_run(
        tenant_id=tenant, run_id=anchored_run, project_id=project_id, trace_id="t", note="重复"
    )
    assert first["idempotent"] is False and second["idempotent"] is True
    assert first["archive_link_id"] == second["archive_link_id"]
    # Still exactly one link — the repeat did not append a second row.
    with psycopg2.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM experience.archive_links WHERE run_id=%s", (str(anchored_run),)
        )
        assert cur.fetchone()[0] == 1


def test_archive_run_rejects_a_project_outside_the_tenant(anchored_run) -> None:
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
    with pytest.raises(ValueError, match="project not found"):
        ExperienceProjector(DB).archive_run(
            tenant_id=tenant, run_id=anchored_run, project_id=uuid4(), trace_id="t"
        )


def test_used_in_joins_observations_to_the_archived_project(anchored_run) -> None:
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)
        with conn.cursor() as cur:
            project_id = _project(cur, tenant)
            _insert_attempt(cur, tenant=tenant, run_id=anchored_run, plugin_version="0.4.2")
        conn.commit()

    projector = ExperienceProjector(DB)
    projector.project_run(
        tenant_id=tenant, run_id=anchored_run, trace_id=f"trace-{anchored_run}", declared_edges=set()
    )
    # Before the anchor the plugin is used somewhere, but attributed to nowhere.
    assert not [u for u in projector.used_in(tenant_id=tenant) if u["plugin_id"] == PLUGIN]

    projector.archive_run(
        tenant_id=tenant, run_id=anchored_run, project_id=project_id, trace_id="t"
    )
    rows = [u for u in projector.used_in(tenant_id=tenant) if u["plugin_id"] == PLUGIN]
    assert len(rows) == 1
    row = rows[0]
    assert row["plugin_version"] == "0.4.2"
    assert row["project_slug"] == PROJECT_SLUG
    assert row["project_id"] == str(project_id)
    assert row["use_count"] == 1 and row["success_count"] == 1


def test_projection_failure_never_breaks_a_run() -> None:
    """The hook is best-effort *by contract*: it swallows and returns ``None``.

    Projection runs after a real plugin run has committed.  If it could raise,
    a knowledge-graph bookkeeping failure would fail a business run — which is
    exactly what ``project_run_best_effort`` exists to prevent.  Pointed at a
    dead endpoint so the failing branch is the one exercised.
    """
    result = project_run_best_effort(
        "postgresql://nobody:nobody@127.0.0.1:1/none",
        tenant_id=uuid4(), run_id=uuid4(), trace_id="t",
    )
    assert result is None


# -- the evidence invariant on proposals ---------------------------------------

EVIDENCE_PLUGIN = "test.exp.archived"
EVIDENCE_OTHER = "test.exp.other"


def _suggestion(tenant_id: UUID, run_ids: list[str], *, contract: str, status: str = "proposed") -> UUID:
    with psycopg2.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            """INSERT INTO experience.relation_suggestions
               (tenant_id,source_plugin_id,target_plugin_id,contract_id,status,
                evidence_count,success_count,fail_count,confidence,weight,evidence_run_ids)
               VALUES(%s,%s,%s,%s,%s,5,5,0,0.5,0.5,%s) RETURNING suggestion_id""",
            (str(tenant_id), EVIDENCE_PLUGIN, EVIDENCE_OTHER, contract, status, Json(run_ids)),
        )
        suggestion_id = UUID(str(cur.fetchone()[0]))
        conn.commit()
    return suggestion_id


def _observation(tenant_id: UUID, run_id: UUID) -> None:
    now = datetime.now(timezone.utc)
    with psycopg2.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            """INSERT INTO experience.node_observations
               (tenant_id,run_id,trace_id,plugin_id,capability,plugin_version,attempt_id,
                attempt_seq,status,observed_at)
               VALUES(%s,%s,%s,%s,%s,'0.1.0',%s,1,'succeeded',%s)""",
            (str(tenant_id), str(run_id), f"trace-{run_id}", EVIDENCE_PLUGIN,
             f"cap.{EVIDENCE_PLUGIN}", uuid4(), now),
        )
        conn.commit()


def _purge_suggestions(*ids: UUID) -> None:
    with psycopg2.connect(MIGRATOR) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', (SELECT id::text FROM iam.tenants WHERE slug='local-dev'), false)")
        cur.fetchone()
        cur.execute(
            "DELETE FROM experience.relation_suggestions WHERE suggestion_id = ANY(%s::uuid[])",
            ([str(i) for i in ids],),
        )
        cur.execute(
            "DELETE FROM experience.node_observations WHERE plugin_id=%s", (EVIDENCE_PLUGIN,)
        )
        conn.commit()


def test_a_proposal_without_surviving_evidence_is_pruned() -> None:
    """A proposal must never advertise evidence that no longer exists.

    The suite purge deletes observations (production never does — they are
    append-only), which is exactly what strands a proposal citing run ids that
    are gone.  Left alone it keeps claiming "5 of 5 succeeded" with nothing
    behind it, and it *grows* on the next projection because
    ``_upsert_suggestion`` refreshes it from ``edge_stats``.  That number is
    shown in the workbench as if it were earned.
    """
    with psycopg2.connect(DB) as conn:
        tenant = _tenant(conn)

    dead_run = uuid4()
    live_run = uuid4()
    dangling = _suggestion(tenant, [str(dead_run)], contract="dead")
    backed = _suggestion(tenant, [str(live_run)], contract="live")
    settled = _suggestion(tenant, [str(dead_run)], contract="settled", status="accepted")
    _observation(tenant, live_run)
    try:
        with psycopg2.connect(MIGRATOR) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            cur.fetchone()
            removed = ExperienceProjector.prune_dangling_suggestions(cur)
            conn.commit()
        assert removed >= 1

        with psycopg2.connect(DB) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            cur.fetchone()
            cur.execute(
                "SELECT suggestion_id FROM experience.relation_suggestions WHERE suggestion_id = ANY(%s::uuid[])",
                ([str(dangling), str(backed), str(settled)],),
            )
            survivors = {str(row[0]) for row in cur.fetchall()}

        assert str(dangling) not in survivors, "a proposal with no surviving evidence must go"
        assert str(backed) in survivors, "a proposal whose evidence survives must stay"
        assert str(settled) in survivors, "a human decision must outlive its evidence"
    finally:
        _purge_suggestions(dangling, backed, settled)
