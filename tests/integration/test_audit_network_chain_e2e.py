"""End-to-end: the 6-node audit network chain runs through the real isolated
executor (child subprocess + verified binding + policy gateway) and lands in
control.node_attempts, proving the ComfyUI-style plugin network compiles AND
executes against the simulation data.

Chain: ledger.csv -> finance-clean -> anomaly-alert -> risk-matrix(6 channels)
                                                        -> issue-type-judge -> issue-amount-compute -> issue-desc-write
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
    "audit.foundation.finance-clean", "audit.risk.finance-anomaly-alert",
    "audit.risk.risk-matrix-build", "audit.finding.issue-type-judge",
    "audit.finding.issue-amount-compute", "audit.report.issue-desc-write",
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
            "node_instance_id": "n-clean", "plugin_id": "audit.foundation.finance-clean",
            "capability": "audit.foundation.finance-clean",
            "input_ports": [_port("raw-finance-set", "input", schema_ref="ledger-artifact-ref")],
            "output_ports": [_port("clean-finance-set", "output", schema_ref="ledger-artifact-ref")],
        },
        {
            "node_instance_id": "n-anomaly", "plugin_id": "audit.risk.finance-anomaly-alert",
            "capability": "audit.risk.finance-anomaly-alert",
            "input_ports": [_port("clean-finance-set", "input", schema_ref="ledger-artifact-ref")],
            "output_ports": [_port("finance-anomaly-set", "output", schema_ref="anomaly-candidates")],
        },
        {
            "node_instance_id": "n-matrix", "plugin_id": "audit.risk.risk-matrix-build",
            "capability": "audit.risk.risk-matrix-build",
            "input_ports": [
                _port(pid, "input", schema_ref="anomaly-candidates")
                for pid in ("policy-risk-set", "industry-risk-set", "ic-risk-set",
                            "finance-anomaly-set", "process-gap-set", "fraud-risk-set")
            ],
            "output_ports": [_port("risk-matrix", "output", schema_ref="metric-series")],
        },
        {
            "node_instance_id": "n-judge", "plugin_id": "audit.finding.issue-type-judge",
            "capability": "audit.finding.issue-type-judge",
            "input_ports": [_port("merged-suspicion", "input", schema_ref="anomaly-candidates")],
            "output_ports": [_port("issue-type-set", "output", schema_ref="finding-draft")],
        },
        {
            "node_instance_id": "n-amount", "plugin_id": "audit.finding.issue-amount-compute",
            "capability": "audit.finding.issue-amount-compute",
            "input_ports": [
                _port("issue-verify-input", "input", schema_ref="finding-draft"),
                _port("clean-finance-set", "input", schema_ref="ledger-artifact-ref"),
            ],
            "output_ports": [_port("issue-amount", "output", schema_ref="finding-draft")],
        },
        {
            "node_instance_id": "n-desc", "plugin_id": "audit.report.issue-desc-write",
            "capability": "audit.report.issue-desc-write",
            "input_ports": [_port("final-issue-set", "input", schema_ref="finding-draft")],
            "output_ports": [_port("issue-desc", "output", schema_ref="report-draft")],
        },
    ]


def _edges() -> list[dict]:
    return [
        {"edge_id": "e1", "source_instance": "n-clean", "source_port": "clean-finance-set",
         "target_instance": "n-anomaly", "target_port": "clean-finance-set"},
        {"edge_id": "e2", "source_instance": "n-anomaly", "source_port": "finance-anomaly-set",
         "target_instance": "n-matrix", "target_port": "finance-anomaly-set"},
        {"edge_id": "e3", "source_instance": "n-anomaly", "source_port": "finance-anomaly-set",
         "target_instance": "n-judge", "target_port": "merged-suspicion"},
        {"edge_id": "e4", "source_instance": "n-judge", "source_port": "issue-type-set",
         "target_instance": "n-amount", "target_port": "issue-verify-input"},
        {"edge_id": "e5", "source_instance": "n-clean", "source_port": "clean-finance-set",
         "target_instance": "n-amount", "target_port": "clean-finance-set"},
        {"edge_id": "e6", "source_instance": "n-amount", "source_port": "issue-amount",
         "target_instance": "n-desc", "target_port": "final-issue-set"},
    ]


def _seed_ref(path: Path) -> ArtifactInput:
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="application/json", sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_confidential",
    )


def _write_seed_channels(staging: Path) -> dict[tuple[str, str], ArtifactInput]:
    out: dict[tuple[str, str], ArtifactInput] = {}
    for i, port_id in enumerate(("policy-risk-set", "industry-risk-set", "ic-risk-set",
                                 "process-gap-set", "fraud-risk-set"), start=1):
        channel = {
            "contract_id": "anomaly-candidates",
            "candidates": [
                {"rule_id": f"{port_id}:seed", "rule_key": "control_gap" if i % 2 else "market_shift",
                 "severity": "medium", "row_ref": f"seed-{i}", "source_ref": port_id, "score": 0.5 + i * 0.05}
            ],
        }
        path = staging / "inputs" / f"{port_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(channel, ensure_ascii=False), encoding="utf-8")
        out[("n-matrix", port_id)] = _seed_ref(path)
    return out


def _ledger_seed(staging: Path) -> ArtifactInput:
    src = SIM / "ledger.csv"
    raw = src.read_bytes()
    path = staging / "inputs" / "ledger.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="text/csv", sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_confidential",
    )


@pytest.fixture()
def tenant_id() -> UUID:
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
        row = cur.fetchone()
        assert row is not None
        return UUID(str(row[0]))


def test_audit_network_chain_executes_end_to_end(tmp_path: Path, tenant_id: UUID) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    seeds: dict[tuple[str, str], ArtifactInput] = {("n-clean", "raw-finance-set"): _ledger_seed(staging)}
    seeds.update(_write_seed_channels(staging))

    plan = compile_plan(
        nodes=_nodes(), edges=_edges(),
        budget={"max_chain_length": 12, "max_candidates": 2000, "max_latency_ms": 60000},
        plan_key=f"plan-audit-chain-{uuid4().hex[:8]}",
        seed_inputs=frozenset(seeds),
    )

    service = TopologyService(TEST_DB, policy=PolicyEngine(allow=_ALLOW))
    result = service.start_plan_run(
        plan,
        seed_inputs=seeds,
        worker_id="audit-chain-test",
        staging_root=staging,
    )

    assert result["status"] == "succeeded", [a for a in result["attempts"] if a["status"] != "succeeded"]
    attempts = {a["node_instance_id"]: a for a in result["attempts"]}
    assert set(attempts) == {"n-clean", "n-anomaly", "n-matrix", "n-judge", "n-amount", "n-desc"}
    assert all(a["status"] == "succeeded" for a in attempts.values())

    # matrix aggregated all six risk channels
    matrix_out = attempts["n-matrix"]["output_refs"]["risk-matrix"]
    matrix = json.loads(Path(url2pathname(matrix_out["uri"][len("file://"):])).read_text(encoding="utf-8"))
    assert len(matrix["channels"]) == 6
    assert len(matrix["matrix"]["points"]) >= 11  # 5 seeds + anomaly candidates

    # judge produced typed issues incl. unbalanced entries
    judge_out = attempts["n-judge"]["output_refs"]["issue-type-set"]
    issues = json.loads(Path(url2pathname(judge_out["uri"][len("file://"):])).read_text(encoding="utf-8"))
    assert issues["summary"]["issue_count"] >= 9
    assert issues["summary"]["by_category"].get("账务差错") == 2

    # amount quantified against the ledger and report composed
    desc_out = attempts["n-desc"]["output_refs"]["issue-desc"]
    report = json.loads(Path(url2pathname(desc_out["uri"][len("file://"):])).read_text(encoding="utf-8"))
    assert report["contract_id"] == "report-draft"
    assert report["summary"]["issue_count"] >= 9
    assert report["summary"]["quantified_total"] > 0

    # all six attempts are visible to the Runs history query
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "SELECT count(*) FROM control.node_attempts WHERE run_id=%s AND status='succeeded'",
            (str(result["run_id"]),),
        )
        assert cur.fetchone()[0] == 6
