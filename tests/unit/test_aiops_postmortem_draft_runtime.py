from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.aiops_postmortem_draft.runtime import InputRejected, handle

PROPOSAL_ID = "9a1b2c3d4e5f6789"
ALERT_FINGERPRINT = "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890"


def _proposal(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_id": "incident-proposal",
        "contract_version": "1.0.0",
        "proposal_id": PROPOSAL_ID,
        "alert_fingerprint": ALERT_FINGERPRINT,
        "status": "proposed",
        "triage": {
            "grouping_key": "affected_system=BCSA_big_01",
            "severity": "critical",
            "affected_system": "BCSA_big_01/equipment-12",
            "summary": "温度传感器越限且连续三分钟无心跳，建议人工确认是否过温停机",
            "evidence_refs": ["file:///G:/数据/04-审计数据集与基准/PCCA-Benchmark/benchmark_cases/bigcase/BCSA/BCSA_big_01/fault_tickets.json"],
            "review_reason": "单源告警，需结合故障工单与日志人工复核",
        },
        "constraints": {"no_execution": True, "no_playbook": True, "requires_human_approval": True},
    }
    payload.update(overrides)
    return payload


def _verification() -> dict[str, object]:
    return {
        "contract_id": "recovery-verification",
        "contract_version": "1.0.0",
        "verification_id": "0a3f6e2b9d1845c7",
        "baseline_sha256": "a" * 64,
        "observed_sha256": "b" * 64,
        "status": "recovered",
        "reference_time": "2026-09-06T11:00:00Z",
        "summary": {
            "baseline_points": 10,
            "observed_points": 10,
            "probes_checked": 2,
            "probes_passed": 2,
            "verdict": "recovered",
            "truncated": False,
        },
        "comparison": {
            "baseline_mean": 1.0,
            "observed_mean": 1.0,
            "relative_delta": 0.0,
            "threshold_ratio": 0.1,
            "within_threshold": True,
            "metrics": [],
        },
        "slo": {
            "availability_target": 0.99,
            "observed_availability": 0.995,
            "error_budget_remaining": 0.5,
            "met": True,
            "probes": [],
        },
        "constraints": {
            "no_execution": True,
            "no_circuit_breaker_change": True,
            "no_change_request": True,
            "ledger_immutable": True,
        },
    }


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


def _envelope(path: Path, *, with_verification: bool = False, **overrides: object) -> dict[str, object]:
    envelope: dict[str, object] = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "aiops.postmortem-draft",
        "capability": "aiops.postmortem.draft",
        "postmortem": {"incident_proposal": _artifact(path)},
    }
    if with_verification:
        verification = path.parent / "recovery-verification-01.json"
        verification.write_text(json.dumps(_verification(), ensure_ascii=False), encoding="utf-8")
        envelope["postmortem"]["verification"] = _artifact(verification)  # type: ignore[index]
    envelope.update(overrides)
    return envelope


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "postmortem-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write(root: Path, name: str, proposal: dict[str, object]) -> Path:
    path = root / name
    path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
    return path


def test_confirmed_proposal_produces_an_unpublished_postmortem_draft(read_roots: Path) -> None:
    path = _write(read_roots, "incident-proposal-01.json", _proposal())
    output = handle(_envelope(path))

    assert output["contract_id"] == "postmortem-draft"
    assert output["contract_version"] == "1.0.0"
    assert output["status"] == "draft"
    assert len(output["postmortem_id"]) == 16
    assert output["incident_refs"] == [f"incident:{PROPOSAL_ID}"]
    assert output["root_cause_candidates"] == ["affected_system=BCSA_big_01"]
    assert output["summary"] == {
        "incidents": 1,
        "severity": "critical",
        "verification_refs": 0,
        "truncated": False,
    }
    assert output["signature"] == {"drafted_by": "aiops.postmortem-draft@0.1.0", "published": False, "publisher": None}
    assert output["constraints"] == {
        "no_auto_publish": True,
        "requires_human_approval": True,
        "immutable_source": True,
    }
    assert output["provenance"]["incident_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert output["provenance"]["verification_sha256"] == ""
    assert output["provenance"]["plugin"] == "aiops.postmortem-draft@0.1.0"
    assert output["timeline_steps"] == [
        {"step": "事件归因", "sequence": 1, "reference": f"incident:{PROPOSAL_ID}"}
    ]


def test_postmortem_draft_is_deterministic_over_the_same_proposal(read_roots: Path) -> None:
    path = _write(read_roots, "incident-proposal-02.json", _proposal())
    first = handle(_envelope(path))
    second = handle(_envelope(path))

    assert first["postmortem_id"] == second["postmortem_id"]
    assert first["title"] == second["title"]
    assert first["overview"] == second["overview"]


def test_optional_verification_extends_timeline_and_provenance(read_roots: Path) -> None:
    path = _write(read_roots, "incident-proposal-03.json", _proposal())
    output = handle(_envelope(path, with_verification=True))

    assert output["summary"]["verification_refs"] == 1
    assert output["verification_refs"] == ["verification:0a3f6e2b9d1845c7"]
    assert output["timeline_steps"] == [
        {"step": "事件归因", "sequence": 1, "reference": f"incident:{PROPOSAL_ID}"},
        {"step": "恢复核验", "sequence": 2, "reference": "verification:0a3f6e2b9d1845c7"},
    ]
    assert len(output["provenance"]["verification_sha256"]) == 64


def test_severity_maps_to_action_priority(read_roots: Path) -> None:
    proposal = _proposal()
    proposal["triage"]["severity"] = "low"  # type: ignore[index]
    path = _write(read_roots, "incident-proposal-04.json", proposal)
    output = handle(_envelope(path))

    assert output["action_items"][0]["priority"] == "low"
    assert output["summary"]["severity"] == "low"


def test_unconfirmed_proposal_is_rejected(read_roots: Path) -> None:
    proposal = _proposal(status="confirmed")
    path = _write(read_roots, "incident-proposal-05.json", proposal)

    with pytest.raises(InputRejected, match="not confirmed"):
        handle(_envelope(path))


def test_tampered_sha256_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "incident-proposal-06.json", _proposal())
    artifact = _artifact(path)
    artifact["sha256"] = "0" * 64

    with pytest.raises(InputRejected, match="sha256 does not match"):
        handle(_envelope(path, postmortem={"incident_proposal": artifact}))


def test_invalid_verification_contract_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "incident-proposal-07.json", _proposal())
    verification = read_roots / "recovery-verification-bad.json"
    verification.write_text(
        json.dumps({"contract_id": "recovery-verification", "contract_version": "2.0.0"}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(InputRejected, match="recovery-verification contract version"):
        handle(
            _envelope(
                path,
                postmortem={
                    "incident_proposal": _artifact(path),
                    "verification": _artifact(verification),
                },
            )
        )
