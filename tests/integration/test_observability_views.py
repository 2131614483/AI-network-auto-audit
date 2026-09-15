"""Run index, failure diagnostics and bundle re-verification, and their API.

These tests pin invariants rather than counts: run rows and evidence bundles are
produced continuously, so any hardcoded total would rot within a week.  The
invariants that matter are the honest-reporting ones — a listing never claims
verification it did not perform, a signature never leaks a raw local path, and
an unlinked run says ``unlinked`` instead of rendering as empty.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.observability.diagnostics import failure_diagnostics
from packages.observability.evidence import export_evidence_bundle
from packages.observability.run_index import run_index, verify_bundle

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")
ROOT = Path(__file__).resolve().parents[2]


def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        return UUID(str(cur.fetchone()[0]))


def _grant(capabilities: list[str]) -> None:
    tenant = _tenant_id()
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s) "
            "ON CONFLICT (tenant_id,name,version) DO UPDATE SET status='active',rules=EXCLUDED.rules",
            (
                tenant,
                f"hub-api-{uuid4().hex}",
                Json([{"rule_id": str(uuid4()), "effect": "allow", "match": {"capabilities": capabilities}}]),
            ),
        )


def _make_bundle(target_dir: Path, run_id: str) -> Path:
    artifact = target_dir / "artifact-source.json"
    artifact.write_text("{}", encoding="utf-8")
    export_evidence_bundle(
        target_dir / f"{run_id}.zip",
        run_id=run_id,
        plan_key="plan-hub-test",
        trace_id=str(uuid4()),
        plan={"plan_key": "plan-hub-test", "nodes": []},
        attempts=[],
        edges=[],
        log_segments=[],
        artifacts=[{"path": str(artifact), "media_type": "application/json"}],
    )
    return target_dir / f"{run_id}.zip"


def test_run_index_reports_both_sides_of_the_join(tmp_path: Path) -> None:
    run_a = str(uuid4())
    bundles_dir = tmp_path / ".data" / "evidence"
    bundles_dir.mkdir(parents=True)
    _make_bundle(bundles_dir, run_a)

    view = run_index(DB, "local-dev", tmp_path, roots=(".data/evidence",))

    summary = view["summary"]
    assert summary["bundles_on_disk"] >= 1
    assert summary["runs_listed"] == len(view["items"])
    assert summary["runs_with_bundle"] + summary["runs_without_bundle"] == summary["runs_listed"]
    assert summary["orphan_bundles"] == len(view["orphan_bundles"])

    by_run = {item["run_id"]: item for item in view["items"]}
    ours = by_run[run_a]
    assert ours["bundle"] is not None
    assert ours["bundle"]["readable"] is True
    assert ours["bundle"]["verified"] is None, "a listing must never claim a verification it did not perform"
    assert ours["plan_key"] == "plan-hub-test"
    assert ours["archive_state"] == "unlinked" and ours["project_id"] is None

    # A run with no bundle still lists, honestly marked.
    phantom = str(uuid4())
    with psycopg2.connect(DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (_tenant_id().__str__(),))
        cur.execute(
            """INSERT INTO control.node_attempts(attempt_id,tenant_id,run_id,plan_key,node_instance_id,
            capability,plugin_id,attempt_seq,status,lease_token,trace_id,execution_hash)
            VALUES(%s,%s,%s,'plan-hub-test','n-1','cap','audit.field.voucher-drilldown',1,'succeeded',%s,%s,'eh')""",
            (str(uuid4()), _tenant_id(), phantom, str(uuid4()), str(uuid4())),
        )
    view2 = run_index(DB, "local-dev", tmp_path, roots=(".data/evidence",))
    phantom_item = next(item for item in view2["items"] if item["run_id"] == phantom)
    assert phantom_item["bundle"] is None and phantom_item["attempts"] == 1
    assert summary["bundles_on_disk"] == view2["summary"]["bundles_on_disk"]


def test_verify_bundle_is_explicit_and_guarded(tmp_path: Path) -> None:
    run_id = str(uuid4())
    bundles_dir = tmp_path / ".data" / "evidence"
    bundles_dir.mkdir(parents=True)
    bundle = _make_bundle(bundles_dir, run_id)

    ok = verify_bundle(run_id, tmp_path, roots=(".data/evidence",))
    assert ok["found"] is True and ok["verification"]["ok"] is True
    assert ok["verification"]["checked"] >= 1

    # Tamper with one member: the recomputed sha must disagree, loudly.
    tampered = tmp_path / ".data" / "evidence-tampered"
    tampered.mkdir(parents=True)
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(tampered)
    (tampered / "attempts.json").write_text('[{"changed": true}]', encoding="utf-8")
    tampered_id = "00000000-0000-4000-8000-000000000000"
    rebuilt = bundles_dir / f"{tampered_id}.zip"
    with zipfile.ZipFile(rebuilt, "w") as archive:
        for path in sorted(tampered.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(tampered).as_posix())
    manifest = json.loads(zipfile.ZipFile(rebuilt).read("manifest.json").decode("utf-8"))
    manifest["items"] = [item for item in manifest["items"] if item["name"] != "attempts.json"]
    with zipfile.ZipFile(rebuilt, "a") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))

    bad = verify_bundle(tampered_id, tmp_path, roots=(".data/evidence",))
    assert bad["found"] is True and bad["verification"]["ok"] is False
    # The tamper tried to launder itself by dropping the manifest entry; the
    # membership cross-check catches exactly that shape.
    assert bad["verification"]["extra"] == ["attempts.json"]

    # Guards: not a uuid, and a uuid with no bundle.
    assert verify_bundle("../../etc/passwd", tmp_path)["found"] is False
    assert verify_bundle("not-a-uuid", tmp_path)["found"] is False
    assert verify_bundle(str(uuid4()), tmp_path, roots=(".data/evidence",))["found"] is False


def test_failure_diagnostics_clusters_by_signature_and_sanitises_messages() -> None:
    view = failure_diagnostics(DB, "local-dev")
    summary, items = view["summary"], view["items"]

    assert summary["scanned"] == sum(item["occurrences"] for item in items)
    assert sum(summary["by_error_kind"].values()) == summary["scanned"]
    assert summary["groups"] == len(items)
    assert items == sorted(items, key=lambda item: (-item["occurrences"], item["signature"]))

    for item in items:
        assert item["run_count"] == len(item["runs"]) <= item["occurrences"]
        assert item["occurrences"] >= item["recent_occurrences"] >= 0
        # Recurrence requires an older onset AND a recent hit, never one event.
        assert item["recurrent"] == (item["recent_occurrences"] > 0 and item["occurrences"] > item["recent_occurrences"])
        assert len(item["samples"]) <= 3
        for sample in item["samples"]:
            assert len(sample["message"]) <= 500
            assert "\x00" not in sample["message"]
        assert item["locate"]["trace_endpoint"] or item["locate"]["bundle_endpoint"]
        assert "re-run" in item["locate"]["note"]

    # Signatures generalise the volatile parts: no raw tenant path may survive.
    for item in items:
        assert "D:\\pythonpro" not in item["signature"]
        assert "C:\\Users" not in item["signature"]


def test_failure_diagnostics_limit_is_visible() -> None:
    full = failure_diagnostics(DB, "local-dev", limit=2000)
    cut = failure_diagnostics(DB, "local-dev", limit=1)
    assert cut["summary"]["truncated"] is (full["summary"]["scanned"] > 1)
    assert cut["summary"]["scanned"] <= 1


def test_observability_api_respects_policy() -> None:
    _grant(["observability.runs.read", "observability.failures.read", "observability.evidence.verify"])
    tenant = _tenant_id()
    client = TestClient(create_app(Settings(database_url=DB)))
    headers = {"X-Tenant-Id": str(tenant), "X-Trace-Id": str(uuid4())}

    runs = client.get("/api/v1/observability/runs", headers=headers)
    assert runs.status_code == 200
    body = runs.json()
    assert body["trace_id"] == headers["X-Trace-Id"]
    assert body["summary"]["runs_listed"] == len(body["items"])

    failures = client.get("/api/v1/observability/failures", headers=headers)
    assert failures.status_code == 200
    fbody = failures.json()
    assert fbody["summary"]["scanned"] == sum(item["occurrences"] for item in fbody["items"])
    assert fbody["totals"]["attempts"] >= fbody["totals"]["failed"] >= 0

    verify = client.get(
        f"/api/v1/observability/bundles/{uuid4()}/verify",
        headers=headers,
    )
    assert verify.status_code == 200
    assert verify.json()["found"] is False

    bad = client.get("/api/v1/observability/bundles/not-a-uuid/verify", headers=headers)
    assert bad.status_code in {200, 422}
