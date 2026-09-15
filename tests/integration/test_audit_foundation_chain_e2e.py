"""End-to-end: the audit foundation batch (quality-check / tag-manage /
metric-compute) runs through the real isolated executor (child subprocess +
verified binding + policy gateway) and lands in control.node_attempts.

Seeds: ledger.csv -> quality-check; tag-query.json -> tag-manage;
ledger.csv -> metric-compute (independent read-only projections).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import url2pathname
from uuid import UUID, uuid4

import psycopg2
import pytest

from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology import port_adapters  # noqa: F401
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

TEST_DB = "postgresql://audit_app:admin@localhost:5432/audit_network_test"
ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / ".data" / "audit-sim"
_SHA64 = "a" * 64

_ALLOW = [
    "topology.chain.execute", "topology.chain.execute.isolated",
    "audit.foundation.quality-check", "audit.foundation.tag-manage",
    "audit.foundation.metric-compute",
]


def _port(port_id: str, direction: str, *, schema_ref: str) -> dict:
    return {
        "port_id": port_id, "direction": direction, "schema_ref": schema_ref,
        "schema_version": "1.0.0", "schema_sha256": _SHA64,
        "media_type": "application/json", "required": True,
        "cardinality": "one", "classification": "internal", "transport": "artifact_ref",
    }


def _nodes() -> list[dict]:
    return [
        {
            "node_instance_id": "n-quality", "plugin_id": "audit.foundation.quality-check",
            "capability": "audit.foundation.quality-check",
            "input_ports": [_port("quality-input", "input", schema_ref="artifact-ref")],
            "output_ports": [_port("quality-report", "output", schema_ref="dataset-validation")],
        },
        {
            "node_instance_id": "n-tags", "plugin_id": "audit.foundation.tag-manage",
            "capability": "audit.foundation.tag-manage",
            "input_ports": [_port("tag-query", "input", schema_ref="artifact-ref")],
            "output_ports": [_port("tag-tree", "output", schema_ref="tag-tree")],
        },
        {
            "node_instance_id": "n-metrics", "plugin_id": "audit.foundation.metric-compute",
            "capability": "audit.foundation.metric-compute",
            "input_ports": [_port("metric-input", "input", schema_ref="artifact-ref")],
            "output_ports": [_port("metric-output", "output", schema_ref="metric-series")],
        },
    ]


def _edges() -> list[dict]:
    return []


def _seed(staging: Path, name: str, raw: bytes, media_type: str) -> ArtifactInput:
    path = staging / "inputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type=media_type, sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_confidential",
    )


@pytest.fixture()
def tenant_id() -> UUID:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


def test_audit_foundation_batch_executes_end_to_end(tmp_path: Path, tenant_id: UUID) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    ledger_raw = (SIM / "ledger.csv").read_bytes()
    tag_query = {
        "object_type": "ledger",
        "dimensions": ["amount-band", "entry-status", "risk-level"],
        "rows": [
            {"entry_id": "T1", "amount": "1200000", "status": "outlier", "severity": "高"},
            {"entry_id": "T2", "amount": "50000", "status": "ok", "severity": "中"},
            {"entry_id": "T3", "amount": "5000", "status": "normal", "severity": "低"},
        ],
    }
    seeds: dict[tuple[str, str], ArtifactInput] = {
        ("n-quality", "quality-input"): _seed(staging, "ledger.csv", ledger_raw, "text/csv"),
        ("n-tags", "tag-query"): _seed(
            staging, "tag-query.json", json.dumps(tag_query, ensure_ascii=False).encode("utf-8"), "application/json",
        ),
        ("n-metrics", "metric-input"): _seed(staging, "ledger-metric.csv", ledger_raw, "text/csv"),
    }

    plan = compile_plan(
        nodes=_nodes(), edges=_edges(),
        budget={"max_chain_length": 12, "max_candidates": 2000, "max_latency_ms": 60000},
        plan_key=f"plan-audit-foundation-{uuid4().hex[:8]}",
        seed_inputs=frozenset(seeds),
    )

    service = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW))
    result = service.start_plan_run(
        plan,
        seed_inputs=seeds,
        worker_id="audit-foundation-test",
        staging_root=staging,
    )

    assert result["status"] == "succeeded", [a for a in result["attempts"] if a["status"] != "succeeded"]
    attempts = {a["node_instance_id"]: a for a in result["attempts"]}
    assert set(attempts) == {"n-quality", "n-tags", "n-metrics"}
    assert all(a["status"] == "succeeded" for a in attempts.values())

    quality_out = attempts["n-quality"]["output_refs"]["quality-report"]
    quality = json.loads(Path(url2pathname(quality_out["uri"][len("file://"):])).read_text(encoding="utf-8"))
    assert quality["contract_id"] == "dataset-validation"
    assert quality["summary"]["checked_rows"] == 96
    assert quality["summary"]["valid"] is False

    tags_out = attempts["n-tags"]["output_refs"]["tag-tree"]
    tags = json.loads(Path(url2pathname(tags_out["uri"][len("file://"):])).read_text(encoding="utf-8"))
    assert tags["contract_id"] == "tag-tree"
    assert tags["total_rows"] == 3

    metrics_out = attempts["n-metrics"]["output_refs"]["metric-output"]
    metrics = json.loads(Path(url2pathname(metrics_out["uri"][len("file://"):])).read_text(encoding="utf-8"))
    assert metrics["contract_id"] == "metric-output"
    assert metrics["metrics"]["row_count"] == 96
    assert metrics["metrics"]["unbalanced_entries"] == 2

    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "SELECT count(*) FROM control.node_attempts WHERE run_id = %s AND status = 'succeeded'",
            (result["run_id"],),
        )
        assert cur.fetchone()[0] == 3
