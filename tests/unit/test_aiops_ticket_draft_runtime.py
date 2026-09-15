from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.aiops_ticket_draft.runtime import InputRejected, handle

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


def _envelope(path: Path, **overrides: object) -> dict[str, object]:
    envelope: dict[str, object] = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "aiops.ticket-draft",
        "capability": "aiops.ticket.draft",
        "ticket": {"incident_proposal": _artifact(path), "target_system": "jira"},
    }
    envelope.update(overrides)
    return envelope


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ticket-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write(root: Path, name: str, proposal: dict[str, object]) -> Path:
    path = root / name
    path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
    return path


def test_confirmed_proposal_produces_an_unsent_ticket_draft(read_roots: Path) -> None:
    path = _write(read_roots, "incident-proposal-01.json", _proposal())
    output = handle(_envelope(path))

    assert output["contract_id"] == "ticket-draft"
    assert output["contract_version"] == "1.0.0"
    assert output["status"] == "draft"
    assert len(output["ticket_id"]) == 16
    assert output["target_system"] == "jira"
    assert output["priority"] == "P1"
    assert output["incident_refs"] == [f"incident:{PROPOSAL_ID}"]
    assert output["signature"] == {"drafted_by": "aiops.ticket-draft@0.1.0", "sent": False, "sender": None}
    assert output["summary"] == {
        "incidents": 1,
        "priority": "P1",
        "evidence_refs": 1,
        "truncated": False,
    }
    assert output["constraints"] == {
        "no_auto_send": True,
        "requires_target_whitelist": True,
        "requires_human_approval": True,
        "immutable_source": True,
    }
    assert output["provenance"]["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert output["provenance"]["plugin"] == "aiops.ticket-draft@0.1.0"


def test_ticket_draft_is_deterministic_over_the_same_proposal(read_roots: Path) -> None:
    path = _write(read_roots, "incident-proposal-02.json", _proposal())
    first = handle(_envelope(path))
    second = handle(_envelope(path))

    assert first["ticket_id"] == second["ticket_id"]
    assert first["title"] == second["title"]
    assert first["description"] == second["description"]


def test_target_system_is_part_of_the_ticket_identity(read_roots: Path) -> None:
    path = _write(read_roots, "incident-proposal-03.json", _proposal())
    jira = handle(_envelope(path, ticket={"incident_proposal": _artifact(path), "target_system": "jira"}))
    servicenow = handle(
        _envelope(path, ticket={"incident_proposal": _artifact(path), "target_system": "servicenow"})
    )

    assert jira["ticket_id"] != servicenow["ticket_id"]
    assert jira["target_system"] == "jira"
    assert servicenow["target_system"] == "servicenow"


def test_severity_maps_to_priority(read_roots: Path) -> None:
    proposal = _proposal()
    proposal["triage"]["severity"] = "low"  # type: ignore[index]
    path = _write(read_roots, "incident-proposal-04.json", proposal)
    output = handle(_envelope(path))

    assert output["priority"] == "P4"
    assert output["summary"]["priority"] == "P4"


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
        handle(_envelope(path, ticket={"incident_proposal": artifact, "target_system": "jira"}))


def test_invalid_target_system_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "incident-proposal-07.json", _proposal())

    with pytest.raises(InputRejected, match="target_system"):
        handle(_envelope(path, ticket={"incident_proposal": _artifact(path), "target_system": "JIRA!!"}))

    with pytest.raises(InputRejected, match="target_system"):
        handle(_envelope(path, ticket={"incident_proposal": _artifact(path), "target_system": ""}))