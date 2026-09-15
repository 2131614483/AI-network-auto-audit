from __future__ import annotations

import os

import pytest

from packages.aiops.engine import AIOpsEngine
from packages.policy.engine import PolicyEngine

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def test_aiops_proposal_is_blocked_without_policy_allow() -> None:
    engine = AIOpsEngine(DB, PolicyEngine(deny=["aiops.playbook.*"]))
    incident = engine.ingest_alert("test-monitor", "phase8-1", "high", {"value": 1})
    proposal = engine.propose(incident, "restart-worker", {"kind": "restart", "target": "worker"})
    execution, status = engine.execute(proposal, mode="dry_run")
    assert str(execution)
    assert status == "blocked"


def test_aiops_low_risk_auto_dry_run_completes() -> None:
    engine = AIOpsEngine(DB, PolicyEngine(auto=True))
    incident = engine.ingest_alert("test-monitor", "phase8-2", "low", {"value": 1})
    proposal = engine.propose(incident, "refresh-cache", {"kind": "refresh"}, risk_class="low")
    _, status = engine.execute(proposal, mode="dry_run")
    assert status == "completed"


def test_aiops_canary_requires_hash_bound_approved_change_request() -> None:
    engine = AIOpsEngine(DB, PolicyEngine(allow=["aiops.playbook.restart-worker"]))
    incident = engine.ingest_alert("test-monitor", "phase8-change", "medium", {"value": 1})
    proposal = engine.propose(incident, "restart-worker", {"kind": "restart", "target": "worker"}, risk_class="medium")
    with pytest.raises(PermissionError):
        engine.execute(proposal, mode="canary")
    change_request = engine.request_change(proposal)
    engine.approve_change(change_request)
    with pytest.raises(ValueError):
        engine.execute(proposal, mode="canary", change_request_id=change_request, command_spec={"kind": "restart", "target": "other"})
    execution, status = engine.execute(proposal, mode="canary", change_request_id=change_request)
    assert status == "completed"
    assert engine.verify(execution, healthy=False) == "rolled_back"
