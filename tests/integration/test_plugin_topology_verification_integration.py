"""Plugin Topology M7 integration tests: run verification (reproducibility).

A verification compares the per-node ``output_checksum`` ledger rows of one
``success`` isolated run against a reference (explicit id or the chain's latest
success run) and appends a ``verified``/``drifted`` evidence row.  The
comparator is pure-DB: it spawns no child process and performs no rollback;
``rollback_verdict`` is a read-only advisory projection only.  Fail-closed:
a non-success run is 409 with zero writes, a missing grant is 403 with zero
subprocesses, a replayed idempotency key returns the existing verification, RLS
hides verifications from other tenants, and ``audit_app`` holds INSERT/SELECT
only (no UPDATE/DELETE).
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
from packages.plugin_topology.verification import RunVerificationError
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


def _service() -> TopologyService:
    return TopologyService(
        DB,
        policy=PolicyEngine(allow=[
            "topology.chain.execute",
            "topology.chain.execute.isolated",
            "topology.chain.verify",
        ]),
    )


def _write_ledger_csv(rows: str) -> Path:
    directory = STAGING / "inputs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"a1-ledger-{uuid4().hex[:8]}.csv"
    path.write_text(rows, encoding="utf-8-sig")
    return path


def _ledger_input_from(path: Path) -> InputSource:
    raw = path.read_bytes()
    artifact = ArtifactInput(
        artifact_id=uuid4(),
        tenant_id=uuid4(),
        uri=path.resolve().as_uri(),
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


_LEDGER_CSV_A = (
    "entry_id,date,account_code,description,debit_amount,credit_amount\n"
    "E1,2026-01-05,1101,现金收款,100.00,100.00\n"
    "E2,2026-01-06,1102,银行划转,200.00,100.00\n"
)

_LEDGER_CSV_B = (
    "entry_id,date,account_code,description,debit_amount,credit_amount\n"
    "E1,2026-01-05,1101,现金收款,100.00,100.00\n"
    "E2,2026-01-06,1102,银行划转,200.00,100.00\n"
    "E3,2026-01-07,1103,备用金,50.00,50.00\n"
)


def _run_request(suffix: str = "") -> dict:
    return {
        "chain_key": _SEED_CHAIN_KEY,
        "mode": "isolated",
        "idempotency_key": f"m7-run-{suffix or uuid4().hex[:8]}",
        "reason": "M7 测试库受限只读演练：种子链 ledger-quality → research-note",
    }


def _verification_request(run_id, reference_run_id, suffix: str = "") -> dict:
    return {
        "run_id": str(run_id),
        "reference_run_id": str(reference_run_id) if reference_run_id else None,
        "reason": "M7 测试库可复现性核查",
        "idempotency_key": f"m7-verify-{suffix or uuid4().hex[:8]}-{uuid4().hex[:6]}",
    }


def _insert_synthetic_run(*, tenant_id: UUID, status: str, chain_key: str = _SEED_CHAIN_KEY) -> UUID:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(
            """INSERT INTO topology.execution_runs
            (tenant_id, chain_id, chain_key, mode, status, reason, idempotency_key, trace_id,
             node_total, chain_checksum, planner_version, finished_at)
            SELECT %s, id, chain_key, 'isolated', %s, 'synthetic row for preflight test',
                   %s, %s, 2, chain_checksum, planner_version, now()
            FROM topology.invocation_chains
            WHERE tenant_id=%s AND chain_key=%s
            RETURNING id""",
            (tenant_id, status, str(uuid4()), str(uuid4()), tenant_id, chain_key),
        )
        return UUID(str(cur.fetchone()[0]))


def _verification_count(*, tenant_id: UUID, run_id: UUID) -> int:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(
            "SELECT COUNT(*) FROM topology.run_verifications WHERE tenant_id=%s AND run_id=%s",
            (tenant_id, run_id),
        )
        return int(cur.fetchone()[0])


# -- verified: reproducible run ---------------------------------------------


def test_verification_verified_when_checksums_match() -> None:
    service = _service()
    csv = _write_ledger_csv(_LEDGER_CSV_A)
    first = service.start_run(_run_request("v1-a"), input_sources={"audit.ledger.validate": _ledger_input_from(csv)})
    second = service.start_run(_run_request("v1-b"), input_sources={"audit.ledger.validate": _ledger_input_from(csv)})
    assert first["status"] == "success" and second["status"] == "success"

    result = service.verify_chain_run(
        _verification_request(second["run_id"], first["run_id"], "v1-ok")
    )
    assert result["idempotent"] is False
    assert result["status"] == "verified"
    assert result["node_total"] == 2
    assert result["node_matched"] == 2
    assert result["node_mismatched"] == 0
    assert result["node_ref_missing"] == 0
    assert result["rollback_verdict"] is None
    # count conservation invariant holds on the evidence row
    assert result["node_matched"] + result["node_mismatched"] + result["node_ref_missing"] == result["node_total"]

    # the projection is listable for the target run
    listing = service.list_chain_verifications(second["run_id"])
    assert any(item["verification_id"] == result["verification_id"] for item in listing)


def test_verification_default_reference_is_latest_success_run() -> None:
    service = _service()
    csv = _write_ledger_csv(_LEDGER_CSV_A)
    older = service.start_run(
        _run_request(f"v2-a-{uuid4().hex[:6]}"),
        input_sources={"audit.ledger.validate": _ledger_input_from(csv)},
    )
    newer = service.start_run(
        _run_request(f"v2-b-{uuid4().hex[:6]}"),
        input_sources={"audit.ledger.validate": _ledger_input_from(csv)},
    )
    assert newer["status"] == "success"

    result = service.verify_chain_run(
        {**_verification_request(newer["run_id"], None, "v2-default"), "reason": "default reference 核查"}
    )
    assert result["status"] == "verified"
    assert result["reference_run_id"] == older["run_id"]


# -- drifted: changed input + read-only rollback verdict ----------------------


def test_verification_drift_produces_rollback_verdict() -> None:
    service = _service()
    baseline = service.start_run(
        _run_request("v3-a"), input_sources={"audit.ledger.validate": _ledger_input_from(_write_ledger_csv(_LEDGER_CSV_A))}
    )
    drifted = service.start_run(
        _run_request("v3-b"), input_sources={"audit.ledger.validate": _ledger_input_from(_write_ledger_csv(_LEDGER_CSV_B))}
    )
    assert baseline["status"] == "success" and drifted["status"] == "success"

    result = service.verify_chain_run(
        _verification_request(drifted["run_id"], baseline["run_id"], "v3-drift")
    )
    assert result["status"] == "drifted"
    assert result["node_total"] == 2
    assert result["node_mismatched"] == 2
    assert result["node_ref_missing"] == 0
    assert result["node_matched"] + result["node_mismatched"] + result["node_ref_missing"] == result["node_total"]

    verdict = result["rollback_verdict"]
    assert verdict is not None
    assert verdict["action"] in ("re-verify", "re-run-locked-release", "escalate-human")
    assert verdict["action"] == "re-run-locked-release"  # mismatch w/o ref gaps
    assert set(verdict["affected_slots"]) == set(_SLOTS)
    assert verdict["baseline"]["reference_run_id"] == baseline["run_id"]
    assert len(verdict["baseline"]["chain_checksum"]) == 64  # sha256 of the locked release
    assert verdict["baseline"]["planner_version"]


# -- fail-closed preflight ----------------------------------------------------


def test_verification_non_success_run_is_rejected_with_zero_writes() -> None:
    service = _service()
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    failed_run_id = _insert_synthetic_run(tenant_id=tenant_id, status="failed")
    # a success run still exists so the failure is not masked by a bad reference
    csv = _write_ledger_csv(_LEDGER_CSV_A)
    good = service.start_run(
        _run_request("v4-good"), input_sources={"audit.ledger.validate": _ledger_input_from(csv)}
    )
    with pytest.raises(RunVerificationError, match="only success runs can be verified"):
        service.verify_chain_run(
            _verification_request(failed_run_id, good["run_id"], "v4-non-success")
        )
    assert _verification_count(tenant_id=tenant_id, run_id=failed_run_id) == 0


def test_verification_requires_verify_capability() -> None:
    no_verify = TopologyService(
        DB,
        policy=PolicyEngine(allow=["topology.chain.execute", "topology.chain.execute.isolated"]),
    )
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    success_run_id = _insert_synthetic_run(tenant_id=tenant_id, status="success")
    with pytest.raises(PermissionError, match="policy denied topology.chain.verify"):
        no_verify.verify_chain_run(_verification_request(success_run_id, None, "v4-no-grant"))
    assert _verification_count(tenant_id=tenant_id, run_id=success_run_id) == 0


def test_api_verification_is_403_without_active_policy() -> None:
    """Seeded `topology.chain.verify` is inactive; the API must fail closed."""
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%topology.%"),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    head = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    response = client.post(
        "/api/v1/topology/runs/00000000-0000-0000-0000-000000000001/verifications",
        headers=head,
        json={"reason": "gate-only probe", "idempotency_key": f"m7-api-403-{uuid4().hex[:8]}"},
    )
    # absent allow-rule maps to a fail-closed response (403 explicit deny /
    # 409 approval-required, M6 run preflight precedent).
    assert response.status_code in (403, 409)
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM topology.run_verifications WHERE tenant_id=%s", (tenant_id,)
        )
        assert cur.fetchone()[0] == 0


# -- idempotency / RLS / privilege surface -------------------------------------


def test_verification_idempotent_replay_returns_existing() -> None:
    service = _service()
    csv = _write_ledger_csv(_LEDGER_CSV_A)
    first = service.start_run(_run_request("v5-a"), input_sources={"audit.ledger.validate": _ledger_input_from(csv)})
    second = service.start_run(_run_request("v5-b"), input_sources={"audit.ledger.validate": _ledger_input_from(csv)})
    request = _verification_request(second["run_id"], first["run_id"], "v5-replay")
    once = service.verify_chain_run(request)
    again = service.verify_chain_run(request)
    assert again["idempotent"] is True
    assert again["verification_id"] == once["verification_id"]
    assert again["status"] == once["status"]

    # a *different* key against the same (run, reference) appends a new row
    another = service.verify_chain_run({**request, "idempotency_key": "m7-verify-v5-other"})
    assert another["verification_id"] != once["verification_id"]


def test_verification_rls_hides_from_other_tenant() -> None:
    service = _service()
    csv = _write_ledger_csv(_LEDGER_CSV_A)
    first = service.start_run(_run_request("v6-a"), input_sources={"audit.ledger.validate": _ledger_input_from(csv)})
    second = service.start_run(_run_request("v6-b"), input_sources={"audit.ledger.validate": _ledger_input_from(csv)})
    result = service.verify_chain_run(
        _verification_request(second["run_id"], first["run_id"], "v6-rls")
    )

    marker = uuid4().hex[:8]
    other_slug = f"m7-verify-{marker}"
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(
            "INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING",
            (other_slug, "M7 验证其他租户"),
        )
    other = TopologyService(
        DB,
        policy=PolicyEngine(allow=["topology.chain.execute", "topology.chain.execute.isolated", "topology.chain.verify"]),
        tenant_slug=other_slug,
    )
    # cross-tenant run lookups are fail-closed: no leak, no empty-listing bypass
    with pytest.raises(ValueError, match="run not found"):
        other.list_chain_verifications(second["run_id"])
    with pytest.raises(ValueError, match="verification not found"):
        other.get_run_verification(result["verification_id"])

    # FORCE RLS bound queries for the other tenant see zero verification rows
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (other_slug,))
        other_id = cur.fetchone()[0]
        cur.execute("SET app.tenant_id = %s", (str(other_id),))
        cur.execute("SELECT COUNT(*) FROM topology.run_verifications")
        assert cur.fetchone()[0] == 0


def test_run_verifications_insert_select_only_for_app_role() -> None:
    """Evidence surface: audit_app can INSERT/SELECT but never UPDATE/DELETE."""
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        for privilege in ("SELECT", "INSERT"):
            cur.execute(
                "SELECT has_table_privilege('audit_app', 'topology.run_verifications', %s)",
                (privilege,),
            )
            assert cur.fetchone()[0] is True, f"audit_app should be granted {privilege}"
        for privilege in ("UPDATE", "DELETE"):
            cur.execute(
                "SELECT has_table_privilege('audit_app', 'topology.run_verifications', %s)",
                (privilege,),
            )
            assert cur.fetchone()[0] is False, f"audit_app must never be granted {privilege}"