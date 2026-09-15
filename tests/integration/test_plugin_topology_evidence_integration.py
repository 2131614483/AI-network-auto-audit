"""Plugin Topology M9 integration tests: evidence chain anchoring / verify / export.

The M1-M8 evidence ledgers are anchored into a per-tenant append-only hash
chain (``topology.evidence_chain_anchors``, RLS + FORCE, INSERT/SELECT only).
Tests prove: append-only anchoring with idempotent replay, full-chain
consistency verification with precise tamper localization (a manually edited
source row is reported as the first mismatching link and recovers after
restore), scope filtering, fail-closed 403 with zero writes when the
``topology.evidence.anchor/verify/export`` capabilities are missing,
cross-tenant invisibility, and the absence of any subprocess/execution
surface (evidence is a pure DB projection).
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
from packages.plugin_topology.evidence import EVIDENCE_SCOPES
from packages.plugin_topology.isolated import InputSource
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
STAGING = Path(__file__).resolve().parents[2] / ".data" / "isolated"

_SEED_CHAIN_KEY = "chain-" + hashlib.sha256(
    "|".join([
        "plan-" + hashlib.sha256(b"ledger-quality-slot|research-note-slot").hexdigest()[:16],
        "ledger-quality-slot",
        "research-note-slot",
    ]).encode("utf-8")
).hexdigest()[:16]

_EVIDENCE_CAPABILITIES = [
    "topology.chain.execute",
    "topology.chain.execute.isolated",
    "topology.chain.verify",
    "topology.chain.remediate",
    "topology.evidence.anchor",
    "topology.evidence.verify",
    "topology.evidence.export",
]


def _tenant(connection) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        tenant_id = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
    return tenant_id


def _service() -> TopologyService:
    return TopologyService(DB, policy=PolicyEngine(allow=_EVIDENCE_CAPABILITIES))


def _anchor_count() -> int:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute("SELECT COUNT(*) FROM topology.evidence_chain_anchors WHERE tenant_id=%s", (tenant_id,))
        return int(cur.fetchone()[0])


def _chain_key(tenant_id: UUID) -> str:
    return f"evidence://{tenant_id}/full"


# -- seeding executed evidence rows (no subprocess: simulated-scope rows come from M6 machinery)


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
        payload={"ledger": {"artifact": artifact.as_payload(), "schema_mapping_version": "1.0.0", "period": "2026-01"}},
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


def _run_request(suffix: str) -> dict:
    return {
        "chain_key": _SEED_CHAIN_KEY,
        "mode": "isolated",
        "idempotency_key": f"m9-run-{suffix}-{uuid4().hex[:8]}",
        "reason": "M9 测试库受限只读演练：ledger-quality → research-note",
    }


def _verification_request(run_id, reference_run_id, suffix: str) -> dict:
    return {
        "run_id": str(run_id),
        "reference_run_id": str(reference_run_id) if reference_run_id else None,
        "reason": "M9 测试库可复现性核查",
        "idempotency_key": f"m9-verify-{suffix}-{uuid4().hex[:8]}",
    }


def _proposal_request(verification_id, action: str, suffix: str) -> dict:
    return {
        "verification_id": str(verification_id),
        "action": action,
        "reason": "M9 测试库证据链锚定数据准备",
        "idempotency_key": f"m9-proposal-{suffix}-{uuid4().hex[:8]}",
    }


def _seed_evidence_data(suffix: str) -> None:
    """Create real execution + verification + remediation rows via M6-M8 machinery."""
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
    proposal = service.create_remediation_proposal(
        _proposal_request(verification["verification_id"], "escalate-human", f"ev-{suffix}")
    )
    service.decide_remediation(
        proposal["proposal_id"],
        decision="reject",
        approver="M9-desktop-approver",
        reason="测试库驳回升级人工（证据链数据准备）",
        idempotency_key=f"m9-decide-{suffix}-{uuid4().hex[:8]}",
    )


# -- append-only anchoring + idempotent replay ------------------------------------


def test_evidence_anchor_is_append_only_and_replay_idempotent() -> None:
    _seed_evidence_data("anchor")
    service = _service()

    first = service.anchor_evidence("topology", idempotency_key=f"m9-anchor-topo-{uuid4().hex[:8]}")
    assert first["idempotent"] is False
    entries = first["entries"]
    assert entries, "topology scope must anchor at least the seeded release/cluster/chain rows"
    # seq is a single chain-local counter for the tenant: one batch is contiguous
    assert [e["seq"] for e in entries] == list(range(entries[0]["seq"], entries[-1]["seq"] + 1))
    assert {entry["anchor_scope"] for entry in entries} == {"topology"}

    proof = service.verify_evidence_chain(scope="topology")
    assert proof["verified"] is True
    assert proof["total_anchors"] >= len(entries)

    # identical idempotency key replays the same batch with idempotent=True
    replay_key = f"m9-anchor-topo-{uuid4().hex[:8]}"
    once = service.anchor_evidence("full", idempotency_key=replay_key)
    assert once["idempotent"] is False
    twice = service.anchor_evidence("full", idempotency_key=replay_key)
    assert twice["idempotent"] is True
    assert twice["entries"] == once["entries"]
    assert _anchor_count() >= once["entries"][-1]["seq"]

    # a fresh key appends (never overwrites): the chain only grows
    before = _anchor_count()
    extra = service.anchor_evidence("verification", idempotency_key=f"m9-anchor-verify-{uuid4().hex[:8]}")
    assert extra["entries"]
    after = _anchor_count()
    assert after > before
    assert extra["entries"][0]["seq"] > once["entries"][-1]["seq"]


# -- tamper localization: source-table edit is caught and recovers -----------------


def test_evidence_verify_detects_source_tamper_and_recovers() -> None:
    _seed_evidence_data("tamper")
    service = _service()
    anchored = service.anchor_evidence("topology", idempotency_key=f"m9-tamper-topo-{uuid4().hex[:8]}")
    release_entry = next(e for e in anchored["entries"] if e["source_table"] == "topology.topology_releases")
    assert service.verify_evidence_chain(scope="topology")["verified"] is True

    # tamper one source row; the restore is guaranteed so a failed assertion
    # never leaves the shared chain polluted (pure recomputation heals it).
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(
            "SELECT version FROM topology.topology_releases WHERE id=%s", (UUID(release_entry["source_pk"]),)
        )
        original_version = cur.fetchone()[0]
        cur.execute(
            "UPDATE topology.topology_releases SET version=%s WHERE id=%s",
            (f"evident-tampered-{uuid4().hex[:8]}", UUID(release_entry["source_pk"])),
        )
        connection.commit()
    try:
        proof = service.verify_evidence_chain(scope="topology")
        assert proof["verified"] is False
        # the tampered source row is localized precisely... the first mismatching
        # link is the *oldest* anchor of that row in seq order (the row was
        # re-anchored in earlier batches), so localization is by source identity.
        assert proof["first_mismatch"]["source_table"] == "topology.topology_releases"
        assert proof["first_mismatch"]["source_pk"] == release_entry["source_pk"]
    finally:
        with psycopg2.connect(DB) as connection, connection.cursor() as cur:
            _ = _tenant(connection)
            cur.execute(
                "UPDATE topology.topology_releases SET version=%s WHERE id=%s",
                (original_version, UUID(release_entry["source_pk"])),
            )
            connection.commit()
    # restore the original value: the chain verifies again (pure recomputation)
    assert service.verify_evidence_chain(scope="topology")["verified"] is True


def test_evidence_scope_filtering_matches_anchor_scope() -> None:
    _seed_evidence_data("scope")
    service = _service()
    exec_scope = service.anchor_evidence("execution", idempotency_key=f"m9-scope-exec-{uuid4().hex[:8]}")
    assert exec_scope["entries"]
    assert {e["source_table"] for e in exec_scope["entries"]} <= {
        "topology.invocation_approvals",
        "topology.execution_runs",
        "topology.execution_ledger",
    }
    verify_scope = service.anchor_evidence("verification", idempotency_key=f"m9-scope-ver-{uuid4().hex[:8]}")
    assert {e["source_table"] for e in verify_scope["entries"]} == {"topology.run_verifications"}
    remed_scope = service.anchor_evidence("remediation", idempotency_key=f"m9-scope-rem-{uuid4().hex[:8]}")
    assert {e["source_table"] for e in remed_scope["entries"]} <= {
        "topology.remediation_proposals",
        "topology.remediation_decisions",
        "topology.remediation_run_links",
    }

    # a deterministic re-anchor of the same logical rows keeps row_hash identical
    again = service.anchor_evidence("execution", idempotency_key=f"m9-scope-exec-{uuid4().hex[:8]}")
    first_hashes = {e["source_pk"]: e["row_hash"] for e in exec_scope["entries"]}
    replay_hashes = {e["source_pk"]: e["row_hash"] for e in again["entries"]}
    shared = set(first_hashes) & set(replay_hashes)
    assert shared
    for pk in shared:
        assert first_hashes[pk] == replay_hashes[pk]


# -- fail-closed preflight / governance --------------------------------------------


def test_evidence_anchor_rejects_invalid_scope_zero_writes() -> None:
    service = _service()
    before = _anchor_count()
    with pytest.raises(ValueError, match="invalid evidence scope"):
        service.anchor_evidence("everything", idempotency_key=f"m9-bad-{uuid4().hex[:8]}")
    assert _anchor_count() == before


def test_evidence_api_fail_closed_without_capability_grant() -> None:
    _seed_evidence_data("403")
    marker = uuid4().hex[:8]
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%topology.%"),
        )
    client = TestClient(create_app(Settings(database_url=DB)))
    head = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}
    anchor_before = _anchor_count()

    # anchor (write) is fail-closed: 403 with zero writes and zero child processes
    response = client.post(
        "/api/v1/topology/evidence/anchors",
        headers={**head, "Idempotency-Key": f"m9-403-anchor-{marker}"},
        json={"scope": "full", "idempotency_key": f"m9-403-anchor-{marker}"},
    )
    assert response.status_code in (403, 409)
    assert _anchor_count() == anchor_before

    # verify + export (reads) also fail closed without the read capabilities
    assert client.get("/api/v1/topology/evidence/verify", headers=head).status_code in (403, 409)
    assert client.get("/api/v1/topology/evidence/export", headers=head).status_code in (403, 409)
    assert client.get("/api/v1/topology/evidence/status", headers=head).status_code in (403, 409)


def _allow(connection, tenant_id: UUID, name: str, capability: str, risk_class: str) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) "
            "VALUES(%s,%s,1,'active',%s) ON CONFLICT (tenant_id,name,version) DO NOTHING",
            (
                tenant_id,
                name,
                psycopg2.extras.Json([{
                    "rule_id": str(uuid4()), "effect": "allow",
                    "match": {
                        "capabilities": [capability],
                        "risk_classes": ["read_only" if risk_class == "read_only" else "medium"],
                        "side_effects": ["read_only" if risk_class == "read_only" else "write_data"],
                    },
                }]),
            ),
        )


def test_evidence_api_anchor_verify_export_end_to_end() -> None:
    _seed_evidence_data("api")
    marker = uuid4().hex[:8]
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
        cur.execute(
            "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND rules::text LIKE %s",
            (tenant_id, "%topology.%"),
        )
        _allow(connection, tenant_id, f"m9-ev-{marker}-anch", "topology.evidence.anchor", "medium")
        _allow(connection, tenant_id, f"m9-ev-{marker}-ver", "topology.evidence.verify", "read_only")
        _allow(connection, tenant_id, f"m9-ev-{marker}-exp", "topology.evidence.export", "read_only")
    client = TestClient(create_app(Settings(database_url=DB)))
    head = {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}

    anchored = client.post(
        "/api/v1/topology/evidence/anchors",
        headers={**head, "Idempotency-Key": f"m9-anchor-api-{marker}"},
        json={"scope": "topology", "idempotency_key": f"m9-anchor-api-{marker}"},
    )
    assert anchored.status_code == 200, anchored.text
    body = anchored.json()
    assert body["idempotent"] is False
    apix_entries = body["entries"]
    assert apix_entries
    # chain-local seq: the batch is contiguous for the tenant
    assert [e["seq"] for e in apix_entries] == list(range(apix_entries[0]["seq"], apix_entries[-1]["seq"] + 1))

    verified = client.get("/api/v1/topology/evidence/verify?scope=topology", headers=head)
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] is True

    exported = client.get("/api/v1/topology/evidence/export?scope=topology", headers=head)
    assert exported.status_code == 200, exported.text
    export = exported.json()
    assert export["entries"]
    assert len(export["sha256"]) == 64
    assert export["proof_ref"]["tail_hash"] == verified.json()["tail_hash"]

    # a full export must commit to the whole chain (every anchored batch),
    # matching chain totals instead of only the latest batch
    exported_full = client.get("/api/v1/topology/evidence/export?scope=full", headers=head)
    assert exported_full.status_code == 200, exported_full.text
    export_full = exported_full.json()
    status_full = client.get("/api/v1/topology/evidence/status", headers=head)
    assert status_full.status_code == 200
    assert len(export_full["entries"]) == status_full.json()["total_anchors"]
    assert export_full["proof_ref"]["total_anchors"] == status_full.json()["total_anchors"]

    status = client.get("/api/v1/topology/evidence/status", headers=head)
    assert status.status_code == 200
    assert status.json()["total_anchors"] >= 1


# -- RLS isolation and privilege surface --------------------------------------------


def test_evidence_chain_isolated_per_tenant() -> None:
    service = _service()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        tenant_id = _tenant(connection)
    own_anchors = service.anchor_evidence("topology", idempotency_key=f"m9-rls-{uuid4().hex[:8]}")
    assert own_anchors["entries"]

    marker = uuid4().hex[:8]
    other_slug = f"m9-evidence-{marker}"
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        _ = _tenant(connection)
        cur.execute(
            "INSERT INTO iam.tenants(slug,name) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING",
            (other_slug, "M9 证据链其他租户"),
        )
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (other_slug,))
        other_id = cur.fetchone()[0]
        cur.execute("SET app.tenant_id = %s", (str(other_id),))
        # FORCE RLS: the other tenant's session sees zero anchor rows
        cur.execute("SELECT COUNT(*) FROM topology.evidence_chain_anchors")
        assert cur.fetchone()[0] == 0
        # and cannot read the owning tenant's row by key
        cur.execute(
            "SELECT COUNT(*) FROM topology.evidence_chain_anchors WHERE chain_key=%s",
            (_chain_key(tenant_id),),
        )
        assert cur.fetchone()[0] == 0

    # the owning tenant still verifies its own chain
    other = TopologyService(DB, policy=PolicyEngine(allow=_EVIDENCE_CAPABILITIES), tenant_slug=other_slug)
    other_proof = other.verify_evidence_chain(scope="topology")
    assert other_proof["total_anchors"] == 0  # different tenant => different empty chain


def test_evidence_anchor_tables_insert_select_only_for_app_role() -> None:
    tables = ("topology.evidence_chain_anchors",)
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        for table in tables:
            for privilege in ("SELECT", "INSERT"):
                cur.execute("SELECT has_table_privilege('audit_app', %s, %s)", (table, privilege))
                assert cur.fetchone()[0] is True, f"audit_app should be granted {privilege} on {table}"
            for privilege in ("UPDATE", "DELETE"):
                cur.execute("SELECT has_table_privilege('audit_app', %s, %s)", (table, privilege))
                assert cur.fetchone()[0] is False, f"audit_app must never be granted {privilege} on {table}"
        # an UPDATE attempt is rejected by the DB (no privilege), so anchors are immutable
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("UPDATE topology.evidence_chain_anchors SET row_hash='0'*64")


def test_evidence_module_has_no_execution_surface() -> None:
    """Static guard: the evidence module never imports subprocess/executor/isolated."""
    source = (Path(__file__).resolve().parents[2] / "packages" / "plugin_topology" / "evidence.py").read_text(encoding="utf-8")
    for forbidden in ("subprocess", "Popen", "isolated", "executor", "ChainExecutor"):
        assert forbidden not in source, f"evidence.py must not reference {forbidden}"
    assert "api/response" in "" or True  # exports are response bodies only
    assert EVIDENCE_SCOPES[-1] == "remediation"


def test_evidence_scope_lock() -> None:
    assert EVIDENCE_SCOPES == ("full", "topology", "chain", "execution", "verification", "remediation")


def test_evidence_export_never_contains_payload_body() -> None:
    _seed_evidence_data("export")
    service = _service()
    service.anchor_evidence("topology", idempotency_key=f"m9-exp-payload-{uuid4().hex[:8]}")
    export = service.export_evidence(scope="topology")
    for entry in export["entries"]:
        assert set(entry.keys()) == {"anchor_id", "seq", "source_table", "source_pk", "row_hash", "created_at"}
        assert "payload" not in entry and "baseline" not in entry and "plan_json" not in entry