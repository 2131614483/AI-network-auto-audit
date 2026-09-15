"""Integration tests for the experience-overlay API (M4).

Policy-gated, tenant-scoped, idempotent. The 0056 baseline policy set
``local-plugin-topology-read`` already ALLOWs the three experience CAPs for
local-dev, so positive cases rely on it (and re-activate it up front); the
fail-closed cases briefly deactivate that set and always restore it in
``finally``, never leaving polluted policy state for other suites.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.experience import ExperienceProjector

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
MIGRATOR = os.getenv("AUDIT_NETWORK_MIGRATOR_DATABASE_URL", "postgresql://audit_migrator:admin@localhost:5432/audit_network_test")

SUGGESTIONS = "/api/v1/topology/nebula-experience/suggestions"
OVERLAY = "/api/v1/topology/plugin-nebula/experience"
REBUILD = "/api/v1/topology/nebula-experience/rebuild"
PRODUCER = "test.expapi.producer"
CONSUMER = "test.expapi.consumer"
BASELINE_SET = "local-plugin-topology-read"


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = UUID(str(cur.fetchone()[0]))
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _set_baseline(connection, tenant_id: UUID, active: bool) -> None:
    status = "active" if active else "inactive"
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "UPDATE policy.policy_sets SET status=%s WHERE tenant_id=%s AND name=%s",
            (status, tenant_id, BASELINE_SET),
        )
    connection.commit()


def _active_principal(connection, tenant_id: UUID) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute("SELECT id FROM iam.principals WHERE tenant_id=%s AND status='active' LIMIT 1", (tenant_id,))
        return UUID(str(cur.fetchone()[0]))


def _first_suggestion_id(connection, tenant_id: UUID) -> str:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "SELECT suggestion_id FROM experience.relation_suggestions "
            "WHERE tenant_id=%s AND source_plugin_id=%s ORDER BY created_at DESC LIMIT 1",
            (str(tenant_id), PRODUCER),
        )
        return str(cur.fetchone()[0])


def _seed_proposed(connection, tenant_id: UUID, run_id: UUID) -> None:
    now = datetime.now(timezone.utc)
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        for instance, plugin, bindings in (
            ("n-a", PRODUCER, {}),
            ("n-b", CONSUMER, {"doc": {"source_instance": "n-a"}}),
        ):
            cur.execute(
                """INSERT INTO control.node_attempts
                   (attempt_id,tenant_id,run_id,plan_key,execution_hash,node_instance_id,capability,
                    plugin_id,attempt_seq,status,input_bindings,output_refs,trace_id,created_at,finished_at)
                   VALUES(%s,%s,%s,'p','h',%s,%s,%s,1,'succeeded',%s,'{}'::jsonb,%s,%s,%s)""",
                (uuid4(), str(tenant_id), str(run_id), instance, f"cap.{plugin}", plugin,
                 Json(bindings), f"trace-{run_id}", now - timedelta(seconds=4), now - timedelta(seconds=2)),
            )
    connection.commit()
    ExperienceProjector(DB).project_run(
        tenant_id=tenant_id, run_id=run_id, trace_id=f"trace-{run_id}", declared_edges=set()
    )


def _cleanup(tenant_id: UUID, run_id: UUID) -> None:
    with psycopg2.connect(MIGRATOR) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        # Governance records produced by L2 solidification (title carries the
        # synthetic plugin id); respect FK order.
        cur.execute("SELECT id FROM knowledge.change_sets WHERE title LIKE '%%test.expapi.%%'")
        cs_ids = [str(r[0]) for r in cur.fetchall()]
        cur.execute(
            "UPDATE experience.relation_suggestions SET release_id=NULL, changeset_id=NULL "
            "WHERE source_plugin_id LIKE 'test.expapi.%%'"
        )
        if cs_ids:
            cur.execute("SELECT id FROM knowledge.releases WHERE changeset_id = ANY(%s::uuid[])", (cs_ids,))
            rel_ids = [str(r[0]) for r in cur.fetchall()]
            if rel_ids:
                cur.execute("UPDATE knowledge.releases SET parent_release_id=NULL WHERE parent_release_id = ANY(%s::uuid[])", (rel_ids,))
                cur.execute("DELETE FROM knowledge.releases WHERE id = ANY(%s::uuid[])", (rel_ids,))
            cur.execute("DELETE FROM knowledge.validation_runs WHERE change_set_id = ANY(%s::uuid[])", (cs_ids,))
        cur.execute("DELETE FROM knowledge.change_operations WHERE payload->>'source_plugin_id' LIKE 'test.expapi.%%'")
        if cs_ids:
            cur.execute("DELETE FROM knowledge.change_sets WHERE id = ANY(%s::uuid[])", (cs_ids,))
        for table in ("edge_observations", "node_observations"):
            cur.execute(f"DELETE FROM experience.{table} WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM control.node_attempts WHERE run_id=%s", (str(run_id),))
        cur.execute("DELETE FROM experience.edge_stats WHERE source_plugin_id LIKE 'test.expapi.%%'")
        cur.execute("DELETE FROM experience.node_stats WHERE plugin_id LIKE 'test.expapi.%%'")
        cur.execute("DELETE FROM experience.relation_suggestions WHERE source_plugin_id LIKE 'test.expapi.%%'")
        conn.commit()


def test_overlay_fail_closed_then_returns_graph_and_overlay() -> None:
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _set_baseline(connection, tenant_id, False)
    try:
        client = TestClient(create_app(Settings(database_url=DB)))
        headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
        assert client.get(OVERLAY, headers=headers).status_code in (403, 409)
        with psycopg2.connect(DB) as connection:
            _set_baseline(connection, tenant_id, True)
        ok = client.get(OVERLAY, headers=headers)
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert set(body) == {"graph", "edge_stats", "node_stats", "suggestions", "used_in", "trace_id"}
        assert set(body["graph"]) == {"stages", "nodes", "edges", "trunk", "layers", "stats", "trace_id"}
        assert isinstance(body["edge_stats"], list)
        assert isinstance(body["suggestions"], list)
        assert isinstance(body["used_in"], list)
    finally:
        with psycopg2.connect(DB) as connection:
            _set_baseline(connection, tenant_id, True)


def test_overlay_domain_scopes_graph_and_evidence() -> None:
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _set_baseline(connection, tenant_id, True)
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}

    quant = client.get(OVERLAY + "?domain=quant", headers=headers)
    assert quant.status_code == 200, quant.text
    body = quant.json()
    node_ids = {node["id"] for node in body["graph"]["nodes"]}
    assert body["graph"]["stats"]["total"] == 5  # quant pack: 5 plugins
    assert all(node_id.startswith("quant.") for node_id in node_ids)
    # the overlay carries only evidence for plugins in the scoped graph, so the
    # audit evidence never leaks into the quant view
    for edge in body["edge_stats"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
    for node in body["node_stats"]:
        assert node["plugin_id"] in node_ids
    for suggestion in body["suggestions"]:
        assert suggestion["source"] in node_ids and suggestion["target"] in node_ids
    for used in body["used_in"]:
        assert used["plugin_id"] in node_ids

    # the scoped graph really differs from the default (audit) one
    default_ids = {n["id"] for n in client.get(OVERLAY, headers=headers).json()["graph"]["nodes"]}
    assert node_ids.isdisjoint(default_ids)


def test_suggestion_decision_requires_capability() -> None:
    run_id = uuid4()
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _set_baseline(connection, tenant_id, True)
        principal = _active_principal(connection, tenant_id)
        _seed_proposed(connection, tenant_id, run_id)
        sid = _first_suggestion_id(connection, tenant_id)
    try:
        with psycopg2.connect(DB) as connection:
            _set_baseline(connection, tenant_id, False)
        client = TestClient(create_app(Settings(database_url=DB)))
        headers = {
            "X-Tenant-Id": str(tenant_id), "X-Actor-Id": str(principal),
            "Idempotency-Key": f"dec-{uuid4().hex}", "X-Trace-Id": str(uuid4()),
        }
        resp = client.post(f"{SUGGESTIONS}/{sid}/decision", json={"decision": "accepted"}, headers=headers)
        assert resp.status_code in (403, 409)
    finally:
        with psycopg2.connect(DB) as connection:
            _set_baseline(connection, tenant_id, True)
        _cleanup(tenant_id, run_id)


def test_suggestion_accept_then_terminal_conflict_and_list_filter() -> None:
    run_id = uuid4()
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _set_baseline(connection, tenant_id, True)
        principal = _active_principal(connection, tenant_id)
        _seed_proposed(connection, tenant_id, run_id)
        sid = _first_suggestion_id(connection, tenant_id)
    try:
        client = TestClient(create_app(Settings(database_url=DB)))
        read_headers = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
        listed = client.get(SUGGESTIONS + "?status=proposed", headers=read_headers)
        assert listed.status_code == 200
        assert any(item["id"] == sid for item in listed.json()["items"])

        decide_headers = {
            "X-Tenant-Id": str(tenant_id), "X-Actor-Id": str(principal),
            "Idempotency-Key": f"accept-{uuid4().hex}", "X-Trace-Id": str(uuid4()),
        }
        accepted = client.post(f"{SUGGESTIONS}/{sid}/decision", json={"decision": "accepted"}, headers=decide_headers)
        assert accepted.status_code == 200, accepted.text
        item = accepted.json()["item"]
        assert item["status"] == "accepted"
        assert item["decided_by"] == str(principal)
        # L2 exit 1: acceptance published a governance release and linked it back.
        assert item["changeset_id"] and item["release_id"] and item["released_at"]
        with psycopg2.connect(DB) as connection:
            _tenant(connection)
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT change_type,status FROM knowledge.change_sets WHERE id=%s",
                    (item["changeset_id"],),
                )
                assert cur.fetchone() == ("experience", "applied")

        conflict_headers = {**decide_headers, "Idempotency-Key": f"dismiss-{uuid4().hex}"}
        conflict = client.post(
            f"{SUGGESTIONS}/{sid}/decision", json={"decision": "dismissed"}, headers=conflict_headers
        )
        assert conflict.status_code == 409

        listed2 = client.get(SUGGESTIONS + "?status=proposed", headers=read_headers).json()
        assert all(item["id"] != sid for item in listed2["items"])
        accepted_list = client.get(SUGGESTIONS + "?status=accepted", headers=read_headers).json()
        assert any(item["id"] == sid for item in accepted_list["items"])
    finally:
        _cleanup(tenant_id, run_id)


def test_rebuild_endpoint_fail_closed_then_ok() -> None:
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _set_baseline(connection, tenant_id, False)
    try:
        client = TestClient(create_app(Settings(database_url=DB)))
        blocked_headers = {
            "X-Tenant-Id": str(tenant_id), "Idempotency-Key": f"rb-block-{uuid4().hex}",
            "X-Trace-Id": str(uuid4()),
        }
        assert client.post(REBUILD, headers=blocked_headers).status_code in (403, 409)
        with psycopg2.connect(DB) as connection:
            _set_baseline(connection, tenant_id, True)
        # fresh idempotency key: the blocked decision above was bound to its key.
        ok_headers = {
            "X-Tenant-Id": str(tenant_id), "Idempotency-Key": f"rb-ok-{uuid4().hex}",
            "X-Trace-Id": str(uuid4()),
        }
        ok = client.post(REBUILD, headers=ok_headers)
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert set(body) == {"edges", "nodes", "trace_id"}
        assert body["edges"] >= 0 and body["nodes"] >= 0
    finally:
        with psycopg2.connect(DB) as connection:
            _set_baseline(connection, tenant_id, True)


def test_overlay_requires_tenant_header() -> None:
    client = TestClient(create_app(Settings(database_url=DB)))
    assert client.get(OVERLAY).status_code == 422


def test_dismissed_suggestion_is_not_solidified() -> None:
    run_id = uuid4()
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _set_baseline(connection, tenant_id, True)
        principal = _active_principal(connection, tenant_id)
        _seed_proposed(connection, tenant_id, run_id)
        sid = _first_suggestion_id(connection, tenant_id)
    try:
        client = TestClient(create_app(Settings(database_url=DB)))
        headers = {
            "X-Tenant-Id": str(tenant_id), "X-Actor-Id": str(principal),
            "Idempotency-Key": f"dismiss-{uuid4().hex}", "X-Trace-Id": str(uuid4()),
        }
        resp = client.post(f"{SUGGESTIONS}/{sid}/decision", json={"decision": "dismissed"}, headers=headers)
        assert resp.status_code == 200, resp.text
        item = resp.json()["item"]
        assert item["status"] == "dismissed"
        assert item["release_id"] is None and item["released_at"] is None
        with psycopg2.connect(DB) as connection:
            _tenant(connection)
            with connection.cursor() as cur:
                cur.execute("SELECT count(*) FROM knowledge.change_sets WHERE title LIKE '%test.expapi.%'")
                assert cur.fetchone()[0] == 0
    finally:
        _cleanup(tenant_id, run_id)


def test_accept_solidification_is_idempotent() -> None:
    run_id = uuid4()
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
        _set_baseline(connection, tenant_id, True)
        principal = _active_principal(connection, tenant_id)
        _seed_proposed(connection, tenant_id, run_id)
        sid = _first_suggestion_id(connection, tenant_id)
    try:
        client = TestClient(create_app(Settings(database_url=DB)))
        first_headers = {
            "X-Tenant-Id": str(tenant_id), "X-Actor-Id": str(principal),
            "Idempotency-Key": f"accept-{uuid4().hex}", "X-Trace-Id": str(uuid4()),
        }
        first = client.post(f"{SUGGESTIONS}/{sid}/decision", json={"decision": "accepted"}, headers=first_headers)
        assert first.status_code == 200, first.text
        release_id = first.json()["item"]["release_id"]
        assert release_id
        # Re-accept with a fresh idempotency key must not create a second release.
        second_headers = {**first_headers, "Idempotency-Key": f"accept2-{uuid4().hex}"}
        second = client.post(f"{SUGGESTIONS}/{sid}/decision", json={"decision": "accepted"}, headers=second_headers)
        assert second.status_code == 200, second.text
        assert second.json()["item"]["release_id"] == release_id
        with psycopg2.connect(DB) as connection:
            _tenant(connection)
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM knowledge.change_operations WHERE payload->>'suggestion_id'=%s",
                    (sid,),
                )
                assert cur.fetchone()[0] == 1
    finally:
        _cleanup(tenant_id, run_id)


# -- project anchor: POST /api/v1/topology/runs/{run_id}/archive ---------------

ARCHIVE = "/api/v1/topology/runs/{run_id}/archive"
ARCHIVE_PROJECT_SLUG = "test-expapi-archive-project"


def _archive_project(connection, tenant_id: UUID) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            """INSERT INTO iam.projects(tenant_id,slug,name) VALUES(%s,%s,'归档端点测试')
               ON CONFLICT (tenant_id,slug) DO UPDATE SET name=EXCLUDED.name RETURNING id""",
            (str(tenant_id), ARCHIVE_PROJECT_SLUG),
        )
        project_id = UUID(str(cur.fetchone()[0]))
    connection.commit()
    return project_id


def _cleanup_archive(run_id: UUID) -> None:
    """The link is append-only for the app role, so the migrator removes it."""
    with psycopg2.connect(MIGRATOR) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant = str(cur.fetchone()[0])
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        cur.fetchone()
        cur.execute("DELETE FROM experience.archive_links WHERE run_id=%s", (str(run_id),))
        cur.execute(
            "DELETE FROM iam.projects WHERE slug=%s AND tenant_id=%s",
            (ARCHIVE_PROJECT_SLUG, tenant),
        )
        connection.commit()


def test_archive_run_endpoint_is_gated_and_idempotent() -> None:
    run_id = uuid4()
    try:
        with psycopg2.connect(DB) as connection:
            tenant_id = _tenant(connection)
            _set_baseline(connection, tenant_id, True)
            project_id = _archive_project(connection, tenant_id)

        client = TestClient(create_app(Settings(database_url=DB)))
        url = ARCHIVE.format(run_id=run_id)
        headers = {
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(uuid4()),
            "Idempotency-Key": f"archive-{run_id.hex}",
        }
        first = client.post(url, json={"project_id": str(project_id), "note": "首轮归档"}, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["idempotent"] is False

        # A fresh idempotency key repeats the call rather than replaying a decision;
        # the link itself is idempotent on (tenant, run, project).
        second = client.post(
            url,
            json={"project_id": str(project_id)},
            headers={**headers, "Idempotency-Key": f"archive2-{run_id.hex}"},
        )
        assert second.status_code == 200, second.text
        assert second.json()["idempotent"] is True
        assert second.json()["archive_link_id"] == first.json()["archive_link_id"]

        unknown = client.post(
            url,
            json={"project_id": str(uuid4())},
            headers={**headers, "Idempotency-Key": f"archive3-{run_id.hex}"},
        )
        assert unknown.status_code == 404, unknown.text

        # Fail closed: without the capability the same call is refused.
        with psycopg2.connect(DB) as connection:
            _set_baseline(connection, tenant_id, False)
        denied = client.post(
            url,
            json={"project_id": str(project_id)},
            headers={**headers, "Idempotency-Key": f"archive4-{run_id.hex}"},
        )
        assert denied.status_code in (403, 409), denied.text
    finally:
        with psycopg2.connect(DB) as connection:
            _set_baseline(connection, _tenant(connection), True)
        _cleanup_archive(run_id)
