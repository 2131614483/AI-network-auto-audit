"""The aiops domain, executed end to end through the real executor.

Composing and compiling aiops was already proven; so was each capability's
payload satisfying its runtime *in process*.  What this closes is the last hop:
the flow goes through ``TopologyService.start_plan_run`` — staged artifacts,
isolated child processes, the policy gate and the execution ledger — and all
seven nodes succeed.

The seeds are the plugin's genuine external inputs, built to the shapes the
runtimes verify (uri/sha256/size, not bare JSON).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.ai_planner.composer import compile_flow, compose_flow, discover_plugins
from packages.ai_planner.domain_pack import load_pack
from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = os.getenv(
    "AUDIT_NETWORK_TEST_DATABASE_URL",
    "postgresql://audit_app:admin@localhost:5432/audit_network_test",
)
STAGING = Path(__file__).resolve().parents[2] / ".data" / "isolated-aiops"

_AIOPS_CAPABILITIES = (
    "aiops.alert.triage",
    "aiops.alert.correlate",
    "aiops.rca.rank",
    "aiops.remediation.propose",
    "aiops.recovery.verify",
    "aiops.postmortem.draft",
    "aiops.ticket.draft",
)
_ALLOW = ["topology.chain.execute", "topology.chain.execute.isolated", *_AIOPS_CAPABILITIES]


def _write(staging: Path, name: str, data: Any) -> ArtifactInput:
    path = staging / "inputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="application/json", sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="internal",
    )


def _ref_dict(seed: ArtifactInput) -> dict[str, Any]:
    return {"uri": seed.uri, "sha256": seed.sha256, "size_bytes": seed.size_bytes}


def _seeds(staging: Path) -> dict[str, ArtifactInput]:
    alert_event = _write(staging, "alert-event.json", {
        "contract_id": "alert-event", "contract_version": "1.0.0",
        "event_id": "EV-1", "fingerprint": "a" * 64,
        "occurred_at": "2026-01-15T10:00:00", "severity": "high",
        "summary": "支付网关 5xx 激增", "source": "prometheus",
        "affected_system": "payment-gateway",
    })
    alert_set = _write(staging, "alert-set.json", [
        {"timestamp": "2026-01-15T10:00:00", "severity": "high",
         "affected_system": "payment-gateway", "summary": "5xx 激增"},
        {"timestamp": "2026-01-15T10:01:00", "severity": "high",
         "affected_system": "payment-gateway", "summary": "5xx 激增"},
    ])
    alert_bundle = _write(staging, "alert-event-set.json", {
        "contract_id": "alert-event-set", "contract_version": "1.0.0",
        "artifact": _ref_dict(alert_set),
    })
    incidents = _write(staging, "incidents.json", [{
        "incident_id": "INC-1", "fault_description": "支付网关 5xx 激增",
        "affected_system": "payment-gateway", "equipment_type": "gateway",
    }])
    topology = _write(staging, "topology.json", {
        "nodes_by_type": {
            "service": [
                {"id": "payment-gateway", "text": "支付网关 服务 5xx 失败"},
                {"id": "db-primary", "text": "主库 连接池 超时"},
            ],
        },
        "edges": {"depends_on": [
            {"source": "payment-gateway", "target": "db-primary",
             "confidence": 0.8, "strength": 0.7},
        ]},
    })
    rca_bundle = _write(staging, "rca-input.json", {
        "contract_id": "rca-input", "contract_version": "1.0.0",
        "incident_set": _ref_dict(incidents),
        "topology_graph": _ref_dict(topology),
    })

    def series(metric: str, values: list[float]) -> dict[str, Any]:
        return {
            "contract_id": "metric-series", "contract_version": "1.0.0",
            "series_id": f"{metric}-1", "metric": metric, "unit": "ms",
            "window_minutes": 60,
            "points": [{"at": f"2026-01-15T10:{i:02d}:00", "value": v}
                       for i, v in enumerate(values)],
        }

    return {
        "alert-event": alert_event,
        "alert-event-set": alert_bundle,
        "rca-input": rca_bundle,
        "baseline-metric-series": _write(staging, "baseline.json", series("latency_p95", [120.0, 130.0])),
        "observed-metric-series": _write(staging, "observed.json", series("latency_p95", [110.0, 112.0])),
    }


def test_the_aiops_chain_runs_through_the_real_executor() -> None:
    pack = load_pack("aiops")
    specs = discover_plugins(globs=pack.globs)
    flow = compose_flow(
        goal="告警处置闭环", select=sorted(specs), plan_key="plan-aiops-e2e", domain="aiops",
    )
    plan = compile_flow(flow)
    assert len(plan.nodes) == 7

    staging = STAGING
    staging.mkdir(parents=True, exist_ok=True)
    by_port = _seeds(staging)

    # `seed_keys(port_id)` is exactly the mapping a caller needs: the plan knows
    # which nodes consume each unfillable input, so nothing has to be guessed.
    seeds: dict[tuple[str, str], ArtifactInput] = {}
    for port_id, artifact in by_port.items():
        keys = plan.seed_keys(port_id)
        assert keys, f"expected the plan to need {port_id!r} as a seed"
        for key in keys:
            seeds[key] = artifact

    service = TopologyService(DB, policy=PolicyEngine(allow=_ALLOW))
    result = service.start_plan_run(
        plan, seed_inputs=seeds, worker_id="aiops-e2e", staging_root=staging,
    )
    attempts = result.get("attempts") or []
    failed = [(a.get("node_instance_id"), a.get("error") or a.get("error_message"))
              for a in attempts if a.get("status") != "succeeded"]
    assert result.get("status") == "succeeded", failed
    assert not failed, failed

    # Every capability in the domain ran, and each produced its own contract.
    succeeded = {a.get("capability") for a in attempts if a.get("status") == "succeeded"}
    assert succeeded == set(_AIOPS_CAPABILITIES), succeeded - set(_AIOPS_CAPABILITIES)

    produced = {
        a.get("capability"): set((a.get("output_refs") or {}).keys())
        for a in attempts
    }
    assert produced["aiops.alert.triage"] == {"incident-proposal"}
    assert produced["aiops.ticket.draft"] == {"ticket-draft"}
    assert produced["aiops.postmortem.draft"] == {"postmortem-draft"}
    assert produced["aiops.recovery.verify"] == {"recovery-verification"}
    assert result.get("run_id"), "the run must be identified"

