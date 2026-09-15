from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.aiops_recovery_verifier.runtime import InputRejected, handle


def _artifact(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "artifact_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "uri": path.resolve().as_uri(),
        "media_type": "application/json",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "classification": "internal",
    }


def _envelope(
    baseline: Path,
    observed: Path,
    *,
    proposal: Path | None = None,
    availability_target: float = 0.99,
    threshold_ratio: float = 1.5,
    probes: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    recovery: dict[str, object] = {
        "baseline": _artifact(baseline),
        "observed": _artifact(observed),
        "availability_target": availability_target,
        "threshold_ratio": threshold_ratio,
        "probes": probes if probes is not None else [],
    }
    if proposal is not None:
        recovery["proposal"] = _artifact(proposal)
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "aiops.recovery-verifier",
        "capability": "aiops.recovery.verify",
        "recovery": recovery,
    }


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "recovery-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _series(root: Path, *, name: str, metric: str = "http_error_rate", values: list[float]) -> Path:
    payload = {
        "contract_id": "metric-series",
        "contract_version": "1.0.0",
        "series_id": f"svc-{name}",
        "metric": metric,
        "unit": "ratio",
        "window_minutes": 60,
        "points": [{"at": f"2026-09-05T08:{index:02d}:00+00:00", "value": value} for index, value in enumerate(values)],
    }
    path = root / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _proposal(root: Path) -> Path:
    payload = {
        "contract_id": "remediation-proposal",
        "contract_version": "1.0.0",
        "proposal_id": "9f2c4e6a1d3b5f8c",
        "rca_candidates_sha256": "a" * 64,
        "status": "proposed",
        "reference_time": "2026-09-05T09:30:00+00:00",
        "summary": {"candidates_processed": 1, "playbook_count": 1, "canary_suggested": True, "rollback_available": True, "truncated": False},
        "constraints": {"no_shell": True, "no_change_request": True, "no_auto_execution": True, "requires_human_approval": True},
        "proposals": [
            {
                "playbook_id": "restart-service",
                "title": "服务/进程重启恢复",
                "root_cause_ref": "RC-0001",
                "candidate_rank": 1,
                "score": 0.8,
                "confidence": 0.8,
                "match_terms": ["服务"],
                "preconditions": [],
                "actions": [{"step": 1, "action_type": "restart", "target": "service", "params": {}, "verify": "健康"}],
                "canary": {"suggested": True, "scope": "single_node", "ratio": 0.1, "watch_minutes": 15, "traffic_criteria": [], "abort_conditions": []},
                "rollback": {"available": True, "strategy": "restart_previous", "criteria": [], "auto_rollback": False},
                "verification": {"expected_signals": ["错误率回落至基线"], "probes": ["http_health"], "watch_minutes": 30},
            }
        ],
    }
    path = root / "proposal.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_recovered_when_observed_near_baseline(read_roots: Path) -> None:
    baseline = _series(read_roots, name="baseline", values=[0.003, 0.002, 0.003])
    observed = _series(read_roots, name="observed", values=[0.0031, 0.0032, 0.003])
    output = handle(_envelope(baseline, observed))
    assert output["contract_id"] == "recovery-verification"
    assert output["status"] == "recovered"
    assert output["comparison"]["within_threshold"] is True
    assert output["slo"]["met"] is True
    assert output["constraints"]["no_execution"] is True
    assert output["constraints"]["no_circuit_breaker_change"] is True


def test_not_recovered_when_observed_deviates(read_roots: Path) -> None:
    baseline = _series(read_roots, name="baseline", values=[0.003, 0.002, 0.003])
    observed = _series(read_roots, name="observed", values=[0.12, 0.15, 0.13])
    output = handle(_envelope(baseline, observed))
    assert output["status"] == "not_recovered"
    assert output["comparison"]["within_threshold"] is False
    assert output["slo"]["met"] is False


def test_probe_failure_blocks_recovery(read_roots: Path) -> None:
    baseline = _series(read_roots, name="baseline", values=[0.003, 0.002, 0.003])
    observed = _series(read_roots, name="observed", values=[0.003, 0.003, 0.003])
    probes = [{"probe": "http_health", "value": 503, "expected": 200}]
    output = handle(_envelope(baseline, observed, probes=probes))
    assert output["status"] == "not_recovered"
    assert output["slo"]["probes"][0]["passed"] is False


def test_insufficient_data_when_series_empty(read_roots: Path) -> None:
    baseline = _series(read_roots, name="baseline", values=[])
    observed = _series(read_roots, name="observed", values=[0.003, 0.003])
    output = handle(_envelope(baseline, observed))
    assert output["status"] == "insufficient_data"


def test_verification_is_deterministic(read_roots: Path) -> None:
    baseline = _series(read_roots, name="baseline", values=[0.003, 0.002, 0.003])
    observed = _series(read_roots, name="observed", values=[0.0031, 0.0032, 0.003])
    first = handle(_envelope(baseline, observed))
    second = handle(_envelope(baseline, observed))
    assert first["verification_id"] == second["verification_id"]
    assert first["status"] == second["status"]


def test_proposal_sha256_is_bound_into_verification_id(read_roots: Path) -> None:
    baseline = _series(read_roots, name="baseline", values=[0.003, 0.002, 0.003])
    observed = _series(read_roots, name="observed", values=[0.003, 0.003, 0.003])
    proposal = _proposal(read_roots)
    with_proposal = handle(_envelope(baseline, observed, proposal=proposal))
    without_proposal = handle(_envelope(baseline, observed))
    assert "proposal_sha256" in with_proposal
    assert with_proposal["verification_id"] != without_proposal["verification_id"]
    assert "proposal_sha256" not in without_proposal


def test_error_metric_drives_availability(read_roots: Path) -> None:
    baseline = _series(read_roots, name="baseline", metric="http_error_rate", values=[0.003, 0.002, 0.003])
    observed = _series(read_roots, name="observed", metric="http_error_rate", values=[0.05, 0.04, 0.05])
    output = handle(_envelope(baseline, observed, availability_target=0.99))
    assert output["status"] == "not_recovered"
    assert output["slo"]["observed_availability"] == 0.953333
    assert output["slo"]["met"] is False


def test_wrong_plugin_identity_is_rejected(read_roots: Path) -> None:
    baseline = _series(read_roots, name="baseline", values=[0.003, 0.002, 0.003])
    observed = _series(read_roots, name="observed", values=[0.003, 0.003, 0.003])
    envelope = _envelope(baseline, observed)
    envelope["plugin_id"] = "aiops.playbook-proposer"
    with pytest.raises(InputRejected):
        handle(envelope)


def test_window_mismatch_is_rejected(read_roots: Path) -> None:
    baseline = _series(read_roots, name="baseline", values=[0.003, 0.002, 0.003])
    _series(read_roots, name="observed", values=[0.003, 0.003, 0.003])
    observed_path = read_roots / "observed.json"
    payload = json.loads(observed_path.read_text(encoding="utf-8"))
    payload["window_minutes"] = 30
    observed_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(InputRejected):
        handle(_envelope(baseline, observed_path))
