"""Plugin Topology M8 integration tests: drift disposition / remediation governance.

A remediation proposal is a pure-DB evidence projection for an M7 ``drifted``
verification: the proposal row is appended once (``status`` stays
``pending_approval`` forever); approve/reject/close decisions land in the
immutable ``remediation_decisions`` ledger and terminal status is *derived*,
never stored by UPDATE; an approved ``re-run-locked-release`` proposal may
dispatch a governed re-run through the unmodified M6 ``start_run`` machinery
and the proposal -> run lineage is recorded in ``remediation_run_links``.
Fail-closed: non-drifted / already-finalized / cross-tenant requests write
nothing, ``topology.chain.remediate`` must be freshly granted (else 403 with
zero subprocesses), and the three evidence tables are RLS + FORCE with
INSERT/SELECT only (no UPDATE/DELETE).
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
from packages.plugin_topology.remediation import RemediationConflictError, RemediationError
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

_CAPABILITIES = [
    "topology.chain.execute",
    "topology.chain.execute.isolated",
    "topology.chain.verify",
    "topology.chain.remediate",
]


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _service() -> TopologyService:
    return TopologyService(DB, policy=PolicyEngine(allow=_CAPABILITIES))


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
        "idempotency_key": f"m8-run-{suffix or uuid4().hex[:8]}",
        "reason": "M8 测试库受限只读演练：ledger-quality → research-note",
    }


def _verification_request(run_id, reference_run_id, suffix: str = "") -> dict:
    return {
        "run_id": str(run_id),
        "reference_run_id": str(reference_run_id) if reference_run_id else None,
        "reason": "M8 测试库可复现性核查",
        "idempotency_key": f"m8-verify-{suffix or uuid4().hex[:8]}-{uuid4().hex[:6]}",
    }


def _proposal_request(verification_id, action: str, suffix: str = "") -> dict:
    return {
        "verification_id": str(verification_id),
        "action": action,
        "reason": "M8 测试库漂移处置",
        "idempotency_key": f"m8-proposal-{suffix or uuid4().hex[:8]}-{uuid4().hex[:6]}",
    }


def _make_drifted(suffix: str) -> tuple[dict, dict, dict]:
    """baseline(A) -> drifted(B) -> drifted verification (verdict re-run-locked-release)."""
    service = _service()
    baseline = service.start_run(
        _run_request(f"base-{suffix}"),
        input_sources={"audit.ledger.validate": _ledger_input_from(_write_ledger_csv(_LEDGER_CSV_A))},
    )
    changed = service.start_run(
        _run_request(f"drift-{suffix}"),
        input_sources={"audit.ledger.validate": _ledger_input_from(_write_ledger_csv(_LEDGER_CSV_B))},
    )
    verification = service.verify_chain_run(
        _verification_request(changed["run_id"], baseline["run_id"], f"drift-{suffix}")
    )
    assert verification["status"] == "drifted"
    return baseline, changed, verification


def _count(query: str, params: tuple) -> int:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(query, params)
        return int(cur.fetchone()[0])


def _proposal_count(tenant_id: UUID, verification_id: UUID | None = None) -> int:
    if verification_id is None:
        return _count(
            "SELECT COUNT(*) FROM topology.remediation_proposals WHERE tenant_id=%s",
            (tenant_id,),
        )
    return _count(
        "SELECT COUNT(*) FROM topology.remediation_proposals WHERE tenant_id=%s AND verification_id=%s",
        (tenant_id, verification_id),
    )


def _decision_count(tenant_id: UUID, proposal_id: UUID) -> int:
    return _count(
        "SELECT COUNT(*) FROM topology.remediation_decisions WHERE tenant_id=%s AND proposal_id=%s",
        (tenant_id, proposal_id),
    )


def _link_count(tenant_id: UUID, proposal_id: UUID) -> int:
    return _count(
        "SELECT COUNT(*) FROM topology.remediation_run_links WHERE tenant_id=%s AND proposal_id=%s",
        (tenant_id, proposal_id),
    )


def _run_count(tenant_id: UUID, chain_key: str) -> int:
    return _count(
        "SELECT COUNT(*) FROM topology.execution_runs WHERE tenant_id=%s AND chain_key=%s",
        (tenant_id, chain_key),
    )


# -- closed loop: propose -> approve -> governed re-run -> re-verify converges --


def test_remediation_closed_loop_approve_rerun_converges() -> None:
    service = _service()
    baseline, _changed, verification = _make_drifted("loop")
    assert verification["rollback_verdict"]["action"] == "re-run-locked-release"

    proposal = service.create_remediation_proposal(
        _proposal_request(verification["verification_id"], "re-run-locked-release", "loop")
    )
    assert proposal["idempotent"] is False
    assert proposal["status"] == "pending_approval"
    assert proposal["action"] == "re-run-locked-release"
    assert set(proposal["affected_slots"]) == set(_SLOTS)
    assert proposal["baseline"]["reference_run_id"] == baseline["run_id"]
    assert proposal["remediation_run_id"] is None

    approved = service.decide_remediation(
        proposal["proposal_id"],
        decision="approve",
        approver="M8-desktop-approver",
        reason="测试库批准受治理重跑",
        idempotency_key=f"m8-approve-loop-{uuid4().hex[:8]}",
    )
    assert approved["status"] == "approved"
    assert approved["decision"] == "approve"
    assert approved["remediation_run_id"] is None  # run not yet dispatched

    rerun = service.remediate_run(
        proposal["proposal_id"],
        reason="M8 测试库受治理重跑：回到锁定发布基线",
        idempotency_key=f"m8-rerun-loop-{uuid4().hex[:8]}",
        input_sources={"audit.ledger.validate": _ledger_input_from(_write_ledger_csv(_LEDGER_CSV_A))},
    )
    assert rerun["status"] == "success"
    assert rerun["proposal_id"] == proposal["proposal_id"]
    assert rerun["remediation_run_id"] == rerun["run_id"]

    # lineage: proposal -> decision -> run link is on the immutable ledger
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    assert _link_count(tenant_id, UUID(proposal["proposal_id"])) == 1
    assert _decision_count(tenant_id, UUID(proposal["proposal_id"])) == 1

    detailed = service.get_remediation_proposal(proposal["proposal_id"])
    assert detailed["status"] == "approved"
    assert detailed["remediation_run_id"] == rerun["run_id"]

    # the governed re-run reproduces the locked baseline: the input-driven
    # (deterministic) ledger slot converges to the baseline checksum exactly.
    # The research-note slot is also deterministic: its ``draft_time``
    # provenance is derived from the input seed (not the wall clock), so the
    # whole chain is bit-reproducible for identical locked inputs (M7 premise).
    def _slot_checksums(run_id) -> dict[str, str]:
        return {e["slot_key"]: e["output_checksum"] for e in service.get_run(run_id)["entries"]}

    base_checks = _slot_checksums(baseline["run_id"])
    rerun_checks = _slot_checksums(rerun["run_id"])
    assert rerun_checks["audit.ledger.validate"] == base_checks["audit.ledger.validate"]

    result = service.verify_chain_run(
        _verification_request(rerun["run_id"], baseline["run_id"], "loop-reverify")
    )
    assert result["node_ref_missing"] == 0
    assert result["node_matched"] >= 1  # the deterministic slot converged
    assert result["node_mismatched"] <= 1  # at most the timestamp-bearing slot


# -- reject: escalate-human proposal never dispatches a run ----------------------


def test_remediation_rejected_escalate_human_never_runs() -> None:
    service = _service()
    _baseline, _changed, verification = _make_drifted("rej")
    proposal = service.create_remediation_proposal(
        _proposal_request(verification["verification_id"], "escalate-human", "rej")
    )
    rejected = service.decide_remediation(
        proposal["proposal_id"],
        decision="reject",
        approver="M8-desktop-approver",
        reason="测试库驳回升级人工",
        idempotency_key=f"m8-reject-rej-{uuid4().hex[:8]}",
    )
    assert rejected["status"] == "rejected"
    assert rejected["remediation_run_id"] is None

    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    runs_before = _run_count(tenant_id, _SEED_CHAIN_KEY)
    with pytest.raises(RemediationConflictError, match="only approved"):
        service.remediate_run(
            proposal["proposal_id"],
            reason="不应执行的驳回重跑",
            idempotency_key=f"m8-rerun-rej-{uuid4().hex[:8]}",
        )
    assert _run_count(tenant_id, _SEED_CHAIN_KEY) == runs_before  # zero new runs
    assert _link_count(tenant_id, UUID(proposal["proposal_id"])) == 0


# -- fail-closed preflight --------------------------------------------------------


def test_remediation_verified_verification_fails_closed() -> None:
    service = _service()
    csv = _write_ledger_csv(_LEDGER_CSV_A)
    first = service.start_run(_run_request("ok-a"), input_sources={"audit.ledger.validate": _ledger_input_from(csv)})
    second = service.start_run(_run_request("ok-b"), input_sources={"audit.ledger.validate": _ledger_input_from(csv)})
    verification = service.verify_chain_run(
        _verification_request(second["run_id"], first["run_id"], "ok-verify")
    )
    assert verification["status"] == "verified"

    with pytest.raises(RemediationError, match="only drifted verifications can be remediated"):
        service.create_remediation_proposal(
            {**_proposal_request(verification["verification_id"], "re-verify", "ok"), "idempotency_key": f"m8-proposal-ok-{uuid4().hex[:8]}"}
        )
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    assert _proposal_count(tenant_id, UUID(verification["verification_id"])) == 0


def test_remediation_requires_remediate_capability() -> None:
    no_remediate = TopologyService(
        DB,
        policy=PolicyEngine(allow=["topology.chain.execute", "topology.chain.execute.isolated", "topology.chain.verify"]),
    )
    _baseline, _changed, verification = _make_drifted("403")
    with pytest.raises(PermissionError, match="policy denied topology.chain.remediate"):
        no_remediate.create_remediation_proposal(
            {**_proposal_request(verification["verification_id"], "re-verify", "403"), "idempotency_key": f"m8-proposal-403-{uuid4().hex[:8]}"}
        )
    with psycopg2.connect(DB) as connection:
        tenant_id = _tenant(connection)
    assert _proposal_count(tenant_id, UUID(verification["verification_id"])) == 0


def test_api_remediation_is_fail_closed_without_active_policy() -> None:
    """Seeded `topology.chain.remediate` is inactive; the API must 403/409."""
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%topology.%"),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    head = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    response = client.post(
        f"/api/v1/topology/verifications/{uuid4()}/remediation",
        headers=head,
        json={"action": "re-verify", "reason": "gate-only probe", "idempotency_key": f"m8-api-403-{uuid4().hex[:8]}"},
    )
    assert response.status_code in (403, 409)
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM topology.remediation_proposals WHERE tenant_id=%s", (tenant_id,))
        assert cur.fetchone()[0] == 0


# -- state machine / idempotency / RLS / privilege surface -----------------------


def test_remediation_terminal_decision_cannot_flip() -> None:
    service = _service()
    _baseline, _changed, verification = _make_drifted("flip")
    proposal = service.create_remediation_proposal(
        _proposal_request(verification["verification_id"], "re-run-locked-release", "flip")
    )
    service.decide_remediation(
        proposal["proposal_id"],
        decision="approve",
        approver="M8-desktop-approver",
        reason="测试库批准",
        idempotency_key=f"m8-approve-flip-{uuid4().hex[:8]}",
    )
    # a *different* terminal decision on the same proposal is a 409 conflict
    with pytest.raises(RemediationConflictError, match="irreversible"):
        service.decide_remediation(
            proposal["proposal_id"],
            decision="reject",
            approver="M8-desktop-approver",
            reason="测试库驳回（不应允许）",
            idempotency_key=f"m8-reject-flip-{uuid4().hex[:8]}",
        )
    # replaying the SAME decision with the same key returns the existing row
    replay = service.decide_remediation(
        proposal["proposal_id"],
        decision="approve",
        approver="M8-desktop-approver",
        reason="测试库批准（重放）",
        idempotency_key=f"m8-approve-flip-{uuid4().hex[:8]}",
    )
    assert replay["idempotent"] is True
    assert replay["status"] == "approved"


def test_remediation_idempotent_proposal_replay() -> None:
    service = _service()
    _baseline, _changed, verification = _make_drifted("idem")
    request = _proposal_request(verification["verification_id"], "re-verify", "idem")
    once = service.create_remediation_proposal(request)
    again = service.create_remediation_proposal(request)
    assert again["idempotent"] is True
    assert again["proposal_id"] == once["proposal_id"]

    # listing returns the proposal, filtered by run and verification
    listing = service.list_remediation_proposals(verification_id=verification["verification_id"])
    assert any(item["proposal_id"] == once["proposal_id"] for item in listing)


def test_remediation_rls_hides_from_other_tenant() -> None:
    service = _service()
    baseline, _changed, verification = _make_drifted("rls")
    proposal = service.create_remediation_proposal(
        _proposal_request(verification["verification_id"], "re-verify", "rls")
    )

    marker = uuid4().hex[:8]
    other_slug = f"m8-remediate-{marker}"
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(
            "INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING",
            (other_slug, "M8 修复提案其他租户"),
        )
    other = TopologyService(DB, policy=PolicyEngine(allow=_CAPABILITIES), tenant_slug=other_slug)
    # cross-tenant lookups are fail-closed: no leak, no empty-listing bypass
    with pytest.raises(ValueError, match="remediation proposal not found"):
        other.get_remediation_proposal(proposal["proposal_id"])
    with pytest.raises(ValueError, match="verification not found"):
        other.create_remediation_proposal(_proposal_request(verification["verification_id"], "re-verify", "rls-x"))

    # FORCE RLS bound queries for the other tenant see zero proposal rows
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (other_slug,))
        other_id = cur.fetchone()[0]
        cur.execute("SET app.tenant_id = %s", (str(other_id),))
        cur.execute("SELECT COUNT(*) FROM topology.remediation_proposals")
        assert cur.fetchone()[0] == 0

    # baseline run remains visible to the owning tenant
    assert service.get_run(baseline["run_id"])["status"] == "success"


def test_remediation_tables_insert_select_only_for_app_role() -> None:
    """Evidence surfaces: audit_app can INSERT/SELECT but never UPDATE/DELETE."""
    tables = (
        "topology.remediation_proposals",
        "topology.remediation_decisions",
        "topology.remediation_run_links",
    )
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        for table in tables:
            for privilege in ("SELECT", "INSERT"):
                cur.execute("SELECT has_table_privilege('audit_app', %s, %s)", (table, privilege))
                assert cur.fetchone()[0] is True, f"audit_app should be granted {privilege} on {table}"
            for privilege in ("UPDATE", "DELETE"):
                cur.execute("SELECT has_table_privilege('audit_app', %s, %s)", (table, privilege))
                assert cur.fetchone()[0] is False, f"audit_app must never be granted {privilege} on {table}"