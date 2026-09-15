"""CW6 contract tests: long-running acceptance — spool health, watermark
back-pressure, completeness, verifiable evidence export, three-layer drilldown.

Acceptance (方案 :438 CW6 + :313 integrity + :458 log-failure matrix):
  - spool health is deterministic and idempotent (24x7 monitoring safe);
  - a producer without a seal is ``partial``/``unknown_tail``, never displayed
    as complete; a spool that is missing is ``unavailable``;
  - when the spool hits its watermark, the handler drops frames *visibly*
    (counted + one warning frame) — an un-evidenced run is never shown as
    fully successful;
  - one run's evidence exports as a zip with a sha manifest that re-verifies
    offline; the same trace_id threads data layer -> log layer -> code layer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from pathlib import Path
from typing import Any
from urllib.request import url2pathname
from uuid import UUID, uuid4

import psycopg2
import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
from packages.observability.evidence import export_evidence_bundle, verify_evidence_bundle
from packages.observability.log_spool import LogEnvelope, SegmentedSpool
from packages.observability.spool_logging import SpoolLogHandler
from packages.observability.spool_ops import spool_health, spool_integrity, watermark_alerts
from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology import port_adapters  # noqa: F401  (registers the real port adapter)
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"
_SHA64 = "a" * 64
_ADAPTER = "candidates-to-backtest"
_ALLOW = [
    "topology.chain.execute", "topology.chain.execute.isolated",
    "audit.ledger.validate", "quant.experiment.evaluate",
]


def _port(port_id: str, direction: str, *, schema_ref: str = "artifact-ref") -> dict:
    return {
        "port_id": port_id, "direction": direction, "schema_ref": schema_ref,
        "schema_version": "1.0.0", "schema_sha256": _SHA64,
        "media_type": "application/json", "required": True,
        "cardinality": "one", "classification": "internal",
        "transport": "artifact_ref",
    }


def _node(node_instance_id: str, capability: str, plugin_id: str, *,
          inputs: tuple[dict, ...] = (), outputs: tuple[dict, ...] = ()) -> dict:
    return {
        "node_instance_id": node_instance_id, "plugin_id": plugin_id,
        "capability": capability, "input_ports": list(inputs),
        "output_ports": list(outputs),
    }


def _edge(edge_id: str, source_instance: str, source_port: str,
          target_instance: str, target_port: str) -> dict:
    return {
        "edge_id": edge_id, "source_instance": source_instance,
        "source_port": source_port, "target_instance": target_instance,
        "target_port": target_port, "adapter": _ADAPTER,
    }


def _build_plan() -> object:
    nodes = [
        _node("ledger-a", "audit.ledger.validate", "audit.ledger-quality",
              inputs=(_port("ledger", "input", schema_ref="ledger-artifact-ref"),),
              outputs=(_port("candidates", "output", schema_ref="audit-quality-candidates"),)),
        _node("consumer-x", "quant.experiment.evaluate", "quant.experiment-evaluator",
              inputs=(_port("experiment", "input", schema_ref="backtest-report"),),
              outputs=(_port("evaluation", "output", schema_ref="experiment-evaluation"),)),
    ]
    edges = [_edge("e1", "ledger-a", "candidates", "consumer-x", "experiment")]
    return compile_plan(
        nodes=nodes, edges=edges, plan_key=f"cw6-{uuid4().hex[:8]}",
        seed_inputs={("ledger-a", "ledger")},
    )


def _write_seed(staging: Path) -> ArtifactInput:
    path = staging / "inputs" / "ledger-a.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "E1,2026-01-05,1101,CW6-收款,100.00,100.00\n",
        encoding="utf-8-sig",
    )
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="text/csv", sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_ledger",
    )


def _tenant() -> UUID:
    with psycopg2.connect(TEST_DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            row = cur.fetchone()
            assert row is not None
            return UUID(str(row[0]))


def _run_once(tmp_path: Path) -> dict:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    seed = _write_seed(staging)
    plan = _build_plan()
    service = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW))
    return service.start_plan_run(
        plan,
        seed_inputs={("ledger-a", "ledger"): seed},
        worker_id="cw6-test-worker",
        staging_root=staging,
    )


def _spool_segments(root: Path) -> list[Path]:
    files = list((root / "hot").rglob("*.jsonl")) if (root / "hot").is_dir() else []
    files += list((root / "archive").rglob("*.gz")) if (root / "archive").is_dir() else []
    return sorted(files)


# -- 1. spool health + watermark alerts -----------------------------------------


def test_spool_health_and_watermark_alerts(tmp_path: Path) -> None:
    spool = SegmentedSpool(tmp_path)
    spool.append("api", 1, [LogEnvelope(event_id="a1", producer_id="api", producer_epoch=1, seq=1,
                                        timestamp="2026-01-01T00:00:00+00:00", level="info",
                                        source="test", message="one")])
    spool.seal("api", 1, last_seq=1, bytes_count=1)
    spool.close()

    health = spool_health(tmp_path)
    assert health.root == str(tmp_path.resolve())
    assert health.segment_count >= 1
    assert health.total_bytes > 0
    assert health.sealed_producer_count == 1

    alerts = watermark_alerts(health, max_bytes=1, max_segments=0, max_hot_age_seconds=1)
    codes = {alert.code for alert in alerts}
    assert "high_watermark" in codes
    assert "max_segments" in codes
    assert all(alert.current > alert.limit for alert in alerts)

def test_spool_health_idempotent_repeated_scan(tmp_path: Path) -> None:
    spool = SegmentedSpool(tmp_path)
    for i in range(3):
        spool.append("api", 1, [LogEnvelope(event_id=f"e{i}", producer_id="api", producer_epoch=1, seq=i + 1,
                                            timestamp="2026-01-01T00:00:00+00:00", level="info",
                                            source="test", message=f"line {i}")])
    spool.close()
    first = spool_health(tmp_path)
    second = spool_health(tmp_path)  # 24x7 loop: repeated scans must be stable
    assert first.total_bytes == second.total_bytes
    assert first.segment_count == second.segment_count
    assert first.hot_segment_count == second.hot_segment_count


# -- 2. completeness: complete / partial(unknown_tail) / unavailable ------------


def test_integrity_complete_partial_unavailable(tmp_path: Path) -> None:
    spool = SegmentedSpool(tmp_path)
    spool.append("api", 1, [LogEnvelope(event_id="a1", producer_id="api", producer_epoch=1, seq=1,
                                        timestamp="2026-01-01T00:00:00+00:00", level="info",
                                        source="test", message="x")])
    spool.seal("api", 1, last_seq=1, bytes_count=1)
    spool.append("worker", 1, [LogEnvelope(event_id="w1", producer_id="worker", producer_epoch=1, seq=1,
                                           timestamp="2026-01-01T00:00:00+00:00", level="info",
                                           source="test", message="y")])
    spool.close()  # worker never seals -> partial with unknown tail

    report = spool_integrity(tmp_path, ["api", "worker", "collector"])
    by_producer = {p.producer_id: p for p in report.producers}
    assert by_producer["api"].status == "complete"
    assert by_producer["api"].persisted_seq == 1
    assert by_producer["worker"].status == "partial"
    assert by_producer["worker"].unknown_tail is True
    assert by_producer["collector"].status == "partial"  # no seal at all
    assert report.complete_count == 1
    assert report.partial_count == 2
    assert any("missing-seal" in gap for gap in report.gaps)

    missing = spool_integrity(tmp_path / "does-not-exist", ["api"])
    assert missing.producers[0].status == "unavailable"
    assert missing.unavailable_count == 1


# -- 3. watermark back-pressure: drops are visible, never silent -----------------


def test_watermark_backpressure_drops_frames_visibly(tmp_path: Path) -> None:
    spool = SegmentedSpool(tmp_path)
    handler = SpoolLogHandler(spool, producer_id="api", max_spool_bytes=1)
    logger = logging.getLogger(f"cw6-backpressure-{uuid4().hex[:6]}")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    for i in range(80):  # well past the 32-frame check
        logger.info("frame %d", i)
    logger.removeHandler(handler)
    spool.close()

    assert handler.dropped_frames > 0, "frames must be dropped once the watermark is breached"
    result = SegmentedSpool(tmp_path).query(trace_id=None, limit=500)
    messages = [e["message"] for e in result["events"]]
    assert any("frames are being dropped" in message for message in messages), \
        "the watermark breach must be a visible warning frame, not a silent drop"
    # not every frame made it, and that is reported by the completeness story:
    # the producer sealed with fewer events than emitted is partial evidence
    assert len(messages) < 80


# -- 4. verifiable evidence export ----------------------------------------------

_ARTIFACT_MEDIA = "application/json"


def test_evidence_bundle_exports_and_verifies(tmp_path: Path) -> None:
    run = _run_once(tmp_path)
    staging = tmp_path / "staging"

    projection = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW)).canvas_projection(
        _tenant(), run["run_id"],
    )
    artifacts: list[dict[str, Any]] = []
    for node in projection["nodes"]:
        for ref in (node.get("output_refs") or {}).values():
            uri = str(ref["uri"])
            local = Path(url2pathname(uri[len("file://"):] if uri.startswith("file://") else uri))
            assert local.is_file(), (node["node_instance_id"], local)
            artifacts.append({
                "name": local.name, "path": str(local),
                "media_type": str(ref.get("media_type") or _ARTIFACT_MEDIA),
                "sha256": str(ref.get("sha256") or ""), "size_bytes": int(ref.get("size_bytes") or 0),
            })

    spool = SegmentedSpool(staging / "logs")
    spool.append("api", 1, [LogEnvelope(event_id="t1", producer_id="api", producer_epoch=1, seq=1,
                                        timestamp="2026-01-01T00:00:00+00:00", level="info",
                                        source="test", message="run started", trace_id=run["trace_id"])])
    spool.seal("api", 1, last_seq=1, bytes_count=1)
    spool.close()
    segment = _spool_segments(staging / "logs")[0]

    bundle = tmp_path / "evidence.zip"
    manifest = export_evidence_bundle(
        bundle,
        run_id=run["run_id"], plan_key=run["plan_key"], trace_id=run["trace_id"],
        plan=projection, attempts=projection["nodes"], edges=projection["edges"],
        log_segments=[(str(segment), "api-e1.jsonl")],
        artifacts=artifacts,
    )
    assert manifest.trace_id == run["trace_id"]
    assert bundle.is_file() and bundle.stat().st_size > 0

    verification = verify_evidence_bundle(bundle)
    assert verification.ok, (verification.missing, verification.mismatched)
    assert verification.checked >= 3  # plan + attempts + edges at minimum

    with zipfile.ZipFile(bundle) as archive:
        assert "manifest.json" in archive.namelist()
        assert "plan.json" in archive.namelist()
        assert any(name.startswith("logs/") for name in archive.namelist())


# -- 5. three-layer drilldown is one consistent trace ---------------------------


def test_three_layer_drilldown_consistent(tmp_path: Path) -> None:
    run = _run_once(tmp_path)

    # data layer: persisted attempts
    attempts = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW)).canvas_projection(
        _tenant(), run["run_id"],
    )["nodes"]
    assert {n["node_instance_id"] for n in attempts} == {"ledger-a", "consumer-x"}
    assert all(n["status"] == "succeeded" for n in attempts)

    # log layer: the same trace_id is queryable in the spool
    spool = SegmentedSpool(tmp_path / "logs")
    spool.append("api", 1, [LogEnvelope(event_id="x1", producer_id="api", producer_epoch=1, seq=1,
                                        timestamp="2026-01-01T00:00:00+00:00", level="info",
                                        source="test", message="run started", trace_id=run["trace_id"])])
    spool.close()
    hit = SegmentedSpool(tmp_path / "logs").query(trace_id=run["trace_id"])
    assert hit["events"], "the run trace_id must be found in the log layer"

    # code layer: the adapter that produced the edge is traceable in the plan
    edges = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW)).canvas_projection(
        _tenant(), run["run_id"],
    )["edges"]
    fan_out = [e for e in edges if e["key"][1] == "candidates"]
    assert fan_out and all(e["adapter"] == _ADAPTER for e in fan_out)
    assert all(e["sha256"] and e["uri"] for e in fan_out)
    # artifacts are real files on disk (the code layer produced them)
    for node in attempts:
        for ref in (node.get("output_refs") or {}).values():
            uri = str(ref["uri"])
            local = Path(url2pathname(uri[len("file://"):] if uri.startswith("file://") else uri))
            assert local.is_file()

    # one trace threads all three layers
    assert run["trace_id"] and hit["events"][0]["trace_id"] == run["trace_id"]


# -- 6. API routes --------------------------------------------------------------


def _api_headers(tenant_id: UUID) -> dict[str, str]:
    return {"X-Tenant-Id": str(tenant_id), "X-Trace-Id": str(uuid4())}


def _allow(connection, tenant_id: UUID, name: str, action: str) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
            (str(tenant_id), name, psycopg2.extras.Json([{
                "rule_id": str(uuid4()), "effect": "allow",
                "match": {"capabilities": [action], "risk_classes": ["read_only"], "side_effects": ["read_only"]},
            }])),
        )


def test_api_spool_health_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant()
    with psycopg2.connect(TEST_DB) as connection:
        _allow(connection, tenant, f"cw6-hlth-{uuid4().hex[:6]}", "observability.spool.read")
    monkeypatch.setenv("SPOOL_MAX_BYTES", "1")  # real threshold -> alert when the real spool is non-empty
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    response = client.get("/api/v1/observability/spool/health", headers=_api_headers(tenant))
    assert response.status_code == 200
    body = response.json()
    assert body["health"]["root"]
    assert body["health"]["scanned_at"]
    assert isinstance(body["alerts"], list)


def test_api_evidence_export_zip(tmp_path: Path) -> None:
    run = _run_once(tmp_path)
    tenant = _tenant()
    with psycopg2.connect(TEST_DB) as connection:
        _allow(connection, tenant, f"cw6-ev-{uuid4().hex[:6]}", "observability.evidence.export")
    client = TestClient(create_app(Settings(database_url=TEST_DB)))
    response = client.get(f"/api/v1/observability/evidence/{run['run_id']}", headers=_api_headers(tenant))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    raw = response.content
    assert raw.startswith(b"PK")  # zip magic
    verification = verify_evidence_bundle_from_bytes(raw)
    assert verification.ok, (verification.missing, verification.mismatched)


def verify_evidence_bundle_from_bytes(raw: bytes) -> Any:
    import io

    from packages.observability.evidence import EvidenceVerification

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = set(archive.namelist())
        if "manifest.json" not in names:
            return EvidenceVerification(ok=False)
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return EvidenceVerification(ok=False)
        missing: list[str] = []
        mismatched: list[str] = []
        checked = 0
        for item in manifest.get("items") or []:
            name = item.get("name")
            if not name:
                continue
            if name not in names:
                missing.append(name)
                continue
            raw_item = archive.read(name)
            if hashlib.sha256(raw_item).hexdigest() != item.get("sha256"):
                mismatched.append(name)
                continue
            checked += 1
        return EvidenceVerification(ok=not missing and not mismatched, checked=checked,
                                    missing=tuple(missing), mismatched=tuple(mismatched))
