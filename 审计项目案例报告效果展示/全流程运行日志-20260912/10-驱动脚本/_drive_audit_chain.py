"""Drive the 6-node audit network chain into the production Runs history.

Real isolated execution (child subprocess + verified binding + policy gate)
against .data/audit-sim/ledger.csv plus five seeded risk channels, writing
6 node_attempts so the desktop Run history + canvas projection can render it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology import port_adapters  # noqa: F401
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = "postgresql://audit_app:admin@localhost:5432/audit_network"
ROOT = Path(r"D:\pythonpro\audit_network")
SIM = ROOT / ".data" / "audit-sim"
STAGING = ROOT / ".data" / "isolated-audit-chain"
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


def _input(path: Path, media_type: str) -> ArtifactInput:
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type=media_type, sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_confidential",
    )


def main() -> None:
    inputs = STAGING / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    seeds: dict[tuple[str, str], ArtifactInput] = {}
    ledger_dst = inputs / "ledger.csv"
    ledger_dst.write_bytes((SIM / "ledger.csv").read_bytes())
    seeds[("n-clean", "raw-finance-set")] = _input(ledger_dst, "text/csv")
    for i, port_id in enumerate(("policy-risk-set", "industry-risk-set", "ic-risk-set",
                                 "process-gap-set", "fraud-risk-set"), start=1):
        channel = {
            "contract_id": "anomaly-candidates",
            "candidates": [
                {"rule_id": f"{port_id}:seed", "rule_key": "control_gap" if i % 2 else "market_shift",
                 "severity": "medium", "row_ref": f"seed-{i}", "source_ref": port_id, "score": 0.5 + i * 0.05}
            ],
        }
        path = inputs / f"{port_id}.json"
        path.write_text(json.dumps(channel, ensure_ascii=False), encoding="utf-8")
        seeds[("n-matrix", port_id)] = _input(path, "application/json")

    plan = compile_plan(
        nodes=_nodes(), edges=_edges(),
        budget={"max_chain_length": 12, "max_candidates": 2000, "max_latency_ms": 60000},
        plan_key="plan-audit-network-chain",
        seed_inputs=frozenset(seeds),
    )
    service = TopologyService(DB, policy=PolicyEngine(allow=_ALLOW))
    result = service.start_plan_run(
        plan, seed_inputs=seeds, worker_id="audit-chain-demo", staging_root=STAGING,
    )
    print("run_id:", result.get("run_id"))
    print("status:", result.get("status"))
    print("plan_key:", result.get("plan_key"))
    for attempt in result.get("attempts") or []:
        print("  attempt:", attempt.get("node_instance_id"), attempt.get("capability"), attempt.get("status"))
    print("OK" if result.get("status") == "succeeded" else "FAILED")


if __name__ == "__main__":
    main()
