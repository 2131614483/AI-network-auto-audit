"""Periodic AIOps cycle generator for 24x7 unattended demo runs.

Each cycle creates one fresh alert -> incident -> remediation proposal ->
change request -> canary execution -> verification chain through the real
AIOpsEngine, so the running system keeps producing new, drillable data instead
of serving a static snapshot.  A random third of cycles verify unhealthy,
exercising the rollback / circuit-open path with real data.
"""

from __future__ import annotations

import random
from typing import Any
from uuid import uuid4

from packages.aiops.engine import AIOpsEngine
from packages.policy.engine import PolicyEngine

_SEVERITIES = ("low", "low", "medium", "high")
_RISK_BY_SEVERITY = {"low": "low", "medium": "medium", "high": "high"}


def generate_aiops_cycle(database_url: str, *, tenant_slug: str = "local-dev", rng: random.Random | None = None) -> dict[str, Any]:
    """Create one complete AIOps simulation cycle; returns its lineage ids."""
    rng = rng or random.Random()
    engine = AIOpsEngine(database_url, PolicyEngine(allow=["aiops.playbook.*"]))
    severity = rng.choice(_SEVERITIES)
    risk_class = _RISK_BY_SEVERITY[severity]
    fingerprint = f"cycle-{uuid4().hex[:10]}"
    incident_id = engine.ingest_alert(
        "demo-monitor", fingerprint, severity,
        {"kind": "synthetic_cycle", "rng_sequence": rng.randrange(1 << 31)},
    )
    proposal_id = engine.propose(
        incident_id, "restart-worker",
        {"kind": "restart", "target": "worker", "cycle": fingerprint},
        risk_class=risk_class,
    )
    change_id = engine.request_change(proposal_id)
    engine.approve_change(change_id)
    execution_id, status = engine.execute(proposal_id, mode="canary", change_request_id=change_id)
    healthy = rng.random() >= 0.33
    outcome = engine.verify(execution_id, healthy=healthy)
    return {
        "incident_id": str(incident_id),
        "proposal_id": str(proposal_id),
        "change_request_id": str(change_id),
        "execution_id": str(execution_id),
        "severity": severity,
        "execution_status": status,
        "verification_outcome": outcome,
    }


def run_demo_generation_once(
    database_url: str, *, tenant_slug: str = "local-dev", cycles: int = 1,
) -> list[dict[str, Any]]:
    """Generate ``cycles`` new AIOps cycles; the worker ticker calls this."""
    if not 1 <= cycles <= 20:
        raise ValueError("cycles must be between 1 and 20")
    return [generate_aiops_cycle(database_url, tenant_slug=tenant_slug) for _ in range(cycles)]
