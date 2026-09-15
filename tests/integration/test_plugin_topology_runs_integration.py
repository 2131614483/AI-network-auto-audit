"""Plugin Topology M6 integration tests: chain-level execution runs.

A run is ONE isolated read-only execution of the release-locked seeded chain
(``audit.ledger.validate -> quant.research-note.draft``) under its own
``run_id``; every per-node ledger row is grouped by that ``run_id`` so the
same chain can be re-drilled with a fresh key (the M5 one-shot semantic is
retired).  Real read-only children run once behind the ISO gate; a replayed
idempotency key returns the recorded run; a concurrent ``running`` run is a
409; a missing ``execute.isolated`` grant is 403 with zero children; RLS hides
runs and their ledger from other tenants.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
from packages.plugin_runtime.db_artifacts import ArtifactInput
from packages.plugin_topology.isolated import InputSource
from packages.plugin_topology.runs import RunConflictError
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
STAGING = Path(__file__).resolve().parents[2] / ".data" / "isolated"

_SLOTS = ("audit.ledger.validate", "quant.research-note.draft")

_SEED_CHAIN_KEY = "chain-" + hashlib.sha256(
    "|".join([
        "plan-" + hashlib.sha256(b"ledger-quality-slot|research-note-slot").hexdigest()[:16],
        "ledger-quality-slot",
        "research-note-slot",
    ]).encode("utf-8")
).hexdigest()[:16]


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _run_service() -> TopologyService:
    return TopologyService(
        DB,
        policy=PolicyEngine(allow=[
            "topology.chain.execute",
            "topology.chain.execute.isolated",
        ]),
    )


def _write_a1_ledger_csv() -> Path:
    """A small, valid audit ledger CSV (deterministic bridge input)."""
    directory = STAGING / "inputs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"a1-ledger-{uuid4().hex[:8]}.csv"
    path.write_text(
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "E1,2026-01-05,1101,现金收款,100.00,100.00\n"
        "E2,2026-01-06,1102,银行划转,200.00,100.00\n",
        encoding="utf-8-sig",
    )
    return path


def _ledger_input() -> InputSource:
    csv_path = _write_a1_ledger_csv()
    raw = csv_path.read_bytes()
    artifact = ArtifactInput(
        artifact_id=uuid4(),
        tenant_id=uuid4(),
        uri=csv_path.resolve().as_uri(),
        media_type="text/csv",
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        classification="audit_ledger",
    )
    return InputSource(
        capability="audit.ledger.validate",
        artifact=artifact,
        payload={
            "ledger": {
                "artifact": artifact.as_payload(),
                "schema_mapping_version": "1.0.0",
                "period": "2026-01",
            }
        },
    )


def _run_request(suffix: str = "") -> dict:
    return {
        "chain_key": _SEED_CHAIN_KEY,
        "mode": "isolated",
        "idempotency_key": f"m6-run-{suffix or uuid4().hex[:8]}",
        "reason": "M6 受限只读演练：种子链 ledger-quality → research-note",
    }


def _insert_running_row(*, tenant_id: UUID, idempotency_key: str) -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(
            """INSERT INTO topology.execution_runs
            (tenant_id, chain_id, chain_key, mode, status, reason, idempotency_key, trace_id,
             node_total, chain_checksum, planner_version)
            SELECT %s, id, chain_key, 'isolated', 'running', 'synthetic running row (409 test)',
                   %s, %s, 2, chain_checksum, planner_version
            FROM topology.invocation_chains
            WHERE tenant_id=%s AND chain_key=%s""",
            (tenant_id, idempotency_key, str(uuid4()), tenant_id, _SEED_CHAIN_KEY),
        )


def _close_running_row(*, tenant_id: UUID, idempotency_key: str) -> None:
    """Move a synthetic running row to a terminal state (evidence is never deleted)."""
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(
            "UPDATE topology.execution_runs SET status='failed', finished_at=now() "
            "WHERE tenant_id=%s AND idempotency_key=%s AND status='running'",
            (tenant_id, idempotency_key),
        )


# -- fail-closed preflight ------------------------------------------------------


def test_run_requires_isolated_mode() -> None:
    # the run-request contract hard-rejects any mode other than isolated
    with pytest.raises(ValueError, match="'isolated' was expected"):
        _run_service().start_run({**_run_request("wrong-mode"), "mode": "simulated"})


def test_run_requires_a_reason() -> None:
    # the run-request contract requires a non-empty reason before any service logic
    with pytest.raises(ValueError, match="should be non-empty"):
        _run_service().start_run({**_run_request("no-reason"), "reason": ""})


def test_run_requires_the_isolated_policy_twin() -> None:
    only_execute = TopologyService(DB, policy=PolicyEngine(allow=["topology.chain.execute"]))
    with pytest.raises(PermissionError, match="policy denied topology.chain.execute.isolated"):
        only_execute.start_run(_run_request("no-twin"))


def test_run_chain_not_found_is_rejected() -> None:
    with pytest.raises(ValueError, match="chain not found"):
        _run_service().start_run({**_run_request("missing"), "chain_key": "chain-" + "f" * 16})


def test_api_run_is_fail_closed_without_policy() -> None:
    """No active allow rule -> 403 and no child process can start."""
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%topology.%"),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    head = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    response = client.post(
        f"/api/v1/topology/chains/{_SEED_CHAIN_KEY}/runs",
        headers=head,
        json=_run_request("api-fail-closed"),
    )
    assert response.status_code in (403, 409)


# -- real drill: state machine + run_id grouping ---------------------------------


def test_run_drill_runs_children_and_groups_ledger_by_run_id() -> None:
    service = _run_service()
    # a fresh idempotency key per invocation: the persistent test database
    # accumulates runs, so a fixed key would replay an old (truncated) run
    result = service.start_run(
        _run_request(),
        input_sources={"audit.ledger.validate": _ledger_input()},
    )
    assert result["mode"] == "isolated"
    assert result["status"] == "success"
    assert result["node_total"] == 2
    assert result["node_succeeded"] == 2
    assert result["node_failed"] == 0
    assert result["finished_at"] is not None
    run_id = UUID(result["run_id"])
    assert [item["slot_key"] for item in result["entries"]] == list(_SLOTS)
    assert all(item["status"] == "succeeded" for item in result["entries"])

    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "SELECT status FROM topology.execution_runs WHERE id=%s AND tenant_id=%s",
            (run_id, tenant_id),
        )
        assert cur.fetchone()[0] == "success"
        cur.execute(
            "SELECT COUNT(*) FROM topology.execution_ledger WHERE tenant_id=%s AND run_id=%s",
            (tenant_id, run_id),
        )
        assert cur.fetchone()[0] == 2
        cur.execute(
            "SELECT COUNT(*) FROM topology.execution_ledger "
            "WHERE tenant_id=%s AND run_id=%s AND run_id IS NOT NULL",
            (tenant_id, run_id),
        )
        assert cur.fetchone()[0] == 2

    # detail view groups the same run's ledger rows
    detail = service.get_run(run_id)
    assert detail["run_id"] == result["run_id"]
    assert detail["status"] == "success"
    assert [item["slot_key"] for item in detail["entries"]] == list(_SLOTS)

    # list view returns the run projection (explicit large limit avoids
    # truncation from the persistent test database accumulating runs)
    listing = service.list_runs(_SEED_CHAIN_KEY, limit=200)
    assert any(item["run_id"] == result["run_id"] for item in listing)


def test_run_replay_same_key_is_idempotent() -> None:
    service = _run_service()
    first = service.start_run(
        _run_request("replay"),
        input_sources={"audit.ledger.validate": _ledger_input()},
    )
    second = service.start_run(
        _run_request("replay"),
        input_sources={"audit.ledger.validate": _ledger_input()},
    )
    assert second["idempotent"] is True
    assert second["run_id"] == first["run_id"]
    assert second["status"] == first["status"]


def test_run_conflict_409_blocks_second_running_run() -> None:
    """One running run per chain: a second run is rejected (409 at the API)."""
    service = _run_service()
    key = f"m6-conflict-{uuid4().hex[:8]}"
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    _insert_running_row(tenant_id=tenant_id, idempotency_key=key)
    try:
        with pytest.raises(RunConflictError, match="already running"):
            service.start_run({**_run_request("conflict"), "idempotency_key": f"m6-other-{uuid4().hex[:8]}"})
    finally:
        _close_running_row(tenant_id=tenant_id, idempotency_key=key)


def test_api_run_conflict_returns_409() -> None:
    """The API maps the same-chain running conflict to HTTP 409."""
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%topology.chain.execute%"),
        )
        marker = f"m6-409-{uuid4().hex[:6]}"
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) "
            "VALUES(%s,%s,2,'active',%s)",
            (
                tenant_id,
                marker,
                psycopg2.extras.Json([
                    {
                        "rule_id": str(uuid4()), "effect": "allow",
                        "match": {
                            "capabilities": ["topology.chain.execute", "topology.chain.execute.isolated"],
                            "risk_classes": ["medium"],
                            "side_effects": ["write_data"],
                        },
                    }
                ]),
            ),
        )
        synthetic_ikey = f"m6-409key-{uuid4().hex[:8]}"
    _insert_running_row(tenant_id=tenant_id, idempotency_key=synthetic_ikey)
    client = TestClient(create_app(Settings(database_url=DB)))
    head = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    response = client.post(
        f"/api/v1/topology/chains/{_SEED_CHAIN_KEY}/runs",
        headers=head,
        json={**_run_request("api-409"), "idempotency_key": f"m6-fresh-{uuid4().hex[:8]}"},
    )
    try:
        assert response.status_code == 409
    finally:
        _close_running_row(tenant_id=tenant_id, idempotency_key=synthetic_ikey)


def test_run_rls_hides_runs_and_ledger_from_other_tenant() -> None:
    marker = uuid4().hex[:8]
    other_slug = f"m6-api-{marker}"
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(
            "INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING",
            (other_slug, "M6 API 其他租户"),
        )
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (other_slug,))
        other_id = cur.fetchone()[0]

    # the other tenant sees zero runs and zero run-ledger rows
    other_service = TopologyService(
        DB,
        policy=PolicyEngine(allow=["topology.chain.execute", "topology.chain.execute.isolated"]),
        tenant_slug=other_slug,
    )
    assert other_service.list_runs(_SEED_CHAIN_KEY) == []
    with pytest.raises(ValueError, match="run not found"):
        other_service.get_run(UUID("00000000-0000-0000-0000-000000000001"))

    # FORCE RLS bound queries for the other tenant find nothing of local-dev
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute("SET app.tenant_id = %s", (str(other_id),))
        cur.execute("SELECT COUNT(*) FROM topology.execution_runs")
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT COUNT(*) FROM topology.execution_ledger e "
            "JOIN topology.invocation_chains c ON c.id=e.chain_id AND c.tenant_id=e.tenant_id "
            "WHERE e.tenant_id=%s AND c.chain_key=%s",
            (other_id, _SEED_CHAIN_KEY),
        )
        assert cur.fetchone()[0] == 0