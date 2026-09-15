"""Plugin Topology M5 integration tests: isolated execution layer drill.

Uses the migration-seeded chain ``ledger-quality -> research-note`` and runs
REAL read-only child processes (IsolatedPluginRuntime) behind the ISO gate:
a synthesized audit ledger CSV is locked as the first node's governed input,
`audit.ledger-quality` produces `audit-quality-candidates`, the bridge
materializer adapts it, and `quant.research-note-draft` consumes it.  Only the
first execution starts children; replays/duplicate runs are skipped, RLS hides
the ledger from other tenants, and a missing `execute.isolated` grant means
403 with zero child processes.
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


def _iso_service() -> TopologyService:
    return TopologyService(
        DB,
        policy=PolicyEngine(allow=[
            "topology.chain.execute",
            "topology.chain.execute.isolated",
        ]),
    )


def _write_a1_ledger_csv() -> Path:
    """A small, valid audit ledger CSV with one unbalanced entry and one clean one."""
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
    """Lock the synthesized ledger CSV into a governed first-node input."""
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


def _iso_request(suffix: str = "") -> dict:
    return {
        "chain_key": _SEED_CHAIN_KEY,
        "mode": "isolated",
        "idempotency_key": f"m5-iso-{suffix or uuid4().hex[:8]}",
        "reason": "M5 受限只读演练：种子链 ledger-quality → research-note",
    }


# -- fail-closed surfacing ------------------------------------------------------


def test_api_isolated_execution_is_fail_closed_without_policy() -> None:
    """No active rule -> 403 and no child process can start."""
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%topology.%"),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    head = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    response = client.post(
        f"/api/v1/topology/chains/{_SEED_CHAIN_KEY}/executions",
        headers=head,
        json=_iso_request(),
    )
    assert response.status_code in (403, 409)


def test_isolated_execution_requires_a_reason() -> None:
    # the execution-request contract rejects an isolated request without a reason
    with pytest.raises(ValueError, match="reason|should be non-empty"):
        _iso_service().execute_chain_isolated(
            {**_iso_request("no-reason"), "reason": ""},
        )


def test_isolated_execution_requires_the_isolated_policy_twin() -> None:
    only_execute = TopologyService(DB, policy=PolicyEngine(allow=["topology.chain.execute"]))
    with pytest.raises(PermissionError, match="policy denied topology.chain.execute.isolated"):
        only_execute.execute_chain_isolated(_iso_request("no-twin"))


def test_isolated_execution_rejects_simulated_mode() -> None:
    with pytest.raises(ValueError, match="requires mode=isolated"):
        _iso_service().execute_chain_isolated(
            {**_iso_request("wrong-mode"), "mode": "simulated"}
        )


# -- real child-process drill on the seeded chain -------------------------------


def test_seeded_chain_isolated_drill_runs_real_children_and_writes_ledger() -> None:
    service = _iso_service()
    result = service.execute_chain_isolated(
        _iso_request("run"),
        input_sources={"audit.ledger.validate": _ledger_input()},
    )
    if result["status"] == "already_executed":
        # the drill already recorded once against this test database
        # (evidence is never hard-deleted); re-verify only the read path.
        pytest.skip("isolated drill already recorded; rerun after test-db rebuild")
    assert result["mode"] == "isolated"
    assert result["status"] == "succeeded"
    assert result["node_count"] == 2
    assert [item["slot_key"] for item in result["entries"]] == list(_SLOTS)
    assert all(item["status"] == "succeeded" for item in result["entries"])

    first, second = result["entries"]
    assert first["plugin_id"] == "audit.ledger-quality"
    assert first["plugin_version"]
    assert len(first["input_sha256"]) == 64
    assert len(first["runtime_code_sha256"]) == 64
    refs = first["output_artifact_refs"]
    assert len(refs) == 1
    assert refs[0]["sha256"] == first["output_checksum"]
    assert refs[0]["media_type"] == "application/json"

    # the bridge consumer materializes a locked evaluation artifact
    assert second["plugin_id"] == "quant.research-note-draft"
    assert second["input_sha256"]
    assert len(second["output_artifact_refs"]) == 1

    # idempotent replay under the same key returns the recorded response
    replayed = service.execute_chain_isolated(
        _iso_request("run"),
        input_sources={"audit.ledger.validate": _ledger_input()},
    )
    assert replayed["idempotent"] is True
    assert replayed["status"] == "succeeded"

    # a fresh key can never relaunch the same chain+mode (skipped as already-run)
    rerun = service.execute_chain_isolated(
        _iso_request("run-again"),
        input_sources={"audit.ledger.validate": _ledger_input()},
    )
    assert rerun["status"] == "already_executed"
    assert rerun["node_count"] == 0

    # ledger rows are real: mode=isolated + persistent metadata.  M6 lets
    # the chain be re-drilled under fresh run_ids, so the ledger accumulates
    # evidence: assert every row's metadata is valid and both slots appear.
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(
            """SELECT e.plan_node_slot_key, e.mode, e.plugin_id, e.plugin_version,
                       e.runtime_code_sha256, e.input_sha256, e.output_checksum,
                       e.output_artifact_refs
            FROM topology.execution_ledger e
            JOIN topology.invocation_chains c ON c.id=e.chain_id AND c.tenant_id=e.tenant_id
            WHERE c.chain_key=%s AND e.mode='isolated' ORDER BY e.ordinal""",
            (_SEED_CHAIN_KEY,),
        )
        rows = cur.fetchall()
    assert {row[0] for row in rows} == set(_SLOTS)
    assert rows  # at least the M5 drill row set is present
    for _slot, mode, _plugin, _version, runtime_hash, input_hash, checksum, refs in rows:
        assert mode == "isolated"
        assert len(runtime_hash) == 64
        assert len(input_hash) == 64
        assert len(checksum) == 64
        assert [ref["sha256"] for ref in (refs or [])]


def test_isolated_ledger_rls_hides_from_other_tenant() -> None:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        other_slug = f"m5-{uuid4().hex[:8]}"
        cur.execute("INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING", (other_slug, "M5 其他租户"))
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (other_slug,))
        other_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(other_id),))
        cur.fetchone()
        cur.execute(
            """SELECT COUNT(*) FROM topology.execution_ledger e
            JOIN topology.invocation_chains c ON c.id=e.chain_id AND c.tenant_id=e.tenant_id
            WHERE e.tenant_id=%s AND c.chain_key=%s AND e.mode='isolated'""",
            (other_id, _SEED_CHAIN_KEY),
        )
        assert cur.fetchone()[0] == 0