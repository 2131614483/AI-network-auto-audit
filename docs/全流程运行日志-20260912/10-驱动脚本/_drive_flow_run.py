"""Drive the AI-written flow file through the real CW3 isolated executor so it
appears in the desktop Runs history + canvas.  Simulated projection only: the
isolated runtime runs the verified read-only builtin plugins against staging
files; nothing touches host/network/infrastructure."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from uuid import uuid4

from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology import port_adapters  # noqa: F401  (registers the real port adapter)
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = "postgresql://audit_app:admin@localhost:5432/audit_network"
_ALLOW = [
    "topology.chain.execute", "topology.chain.execute.isolated",
    "audit.ledger.validate", "audit.finding.draft",
]
ROOT = Path(r"D:\pythonpro\audit_network")
FLOW = ROOT / ".data" / "ai-written-flow.json"
STAGING = ROOT / ".data" / "isolated-demo"


def _ledger_csv() -> str:
    # two identical rows -> duplicate_row candidate -> finding.draft emits a
    # deterministic duplicate-entry finding draft (both plugins share the rule).
    return (
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "E1,2026-01-05,1101,收款,100.00,100.00\n"
        "E1,2026-01-05,1101,收款,100.00,100.00\n"
    )


def main() -> None:
    d = json.loads(FLOW.read_text(encoding="utf-8"))
    plan = compile_plan(
        nodes=d["nodes"], edges=d["edges"], budget=d.get("budget"),
        plan_key=d["plan_key"],
        seed_inputs={tuple(pair) for pair in d.get("seed_inputs", [])},
    )
    print("compiled:", plan.plan_key, "| nodes:", len(plan.nodes), "| edges:", len(plan.edges))

    inputs_dir = STAGING / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    seed_path = inputs_dir / "ledger-a.csv"
    seed_path.write_text(_ledger_csv(), encoding="utf-8-sig")
    raw = seed_path.read_bytes()
    seed = {
        ("ledger-validate-001", "ledger"): ArtifactInput(
            artifact_id=uuid4(), tenant_id=uuid4(), uri=seed_path.resolve().as_uri(),
            media_type="text/csv", sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw), classification="audit_ledger",
        )
    }

    service = TopologyService(DB, policy=PolicyEngine(allow=_ALLOW))
    run = service.start_plan_run(
        plan,
        seed_inputs=seed,
        worker_id="demo-ai-flow",
        staging_root=STAGING,
        trace_id=uuid4(),
    )
    print("run_id:", run.get("run_id"))
    print("status:", run.get("status"))
    print("plan_key:", run.get("plan_key"))
    print("execution_hash:", run.get("execution_hash"))
    for attempt in run.get("entries") or []:
        print("  attempt:", attempt.get("node_instance_id"), attempt.get("status"))
    print("OK")


if __name__ == "__main__":
    main()
