"""Drive the audit foundation batch (quality-check / tag-manage / metric-compute)
into the production Runs history with the real isolated executor."""
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
STAGING = ROOT / ".data" / "isolated-audit-foundation"
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


def _seed(staging: Path, name: str, raw: bytes, media_type: str) -> ArtifactInput:
    path = staging / "inputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type=media_type, sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_confidential",
    )


def main() -> None:
    staging = STAGING
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
        nodes=_nodes(), edges=[],
        budget={"max_chain_length": 12, "max_candidates": 2000, "max_latency_ms": 60000},
        plan_key="plan-audit-foundation-batch",
        seed_inputs=frozenset(seeds),
    )
    service = TopologyService(DB, policy=PolicyEngine(allow=_ALLOW))
    result = service.start_plan_run(
        plan, seed_inputs=seeds, worker_id="audit-foundation-demo", staging_root=staging,
    )
    print("run_id:", result.get("run_id"))
    print("status:", result.get("status"))
    print("plan_key:", result.get("plan_key"))
    for attempt in result.get("attempts") or []:
        print("  attempt:", attempt.get("node_instance_id"), attempt.get("capability"), attempt.get("status"))
    print("OK" if result.get("status") == "succeeded" else "FAILED")


if __name__ == "__main__":
    main()
