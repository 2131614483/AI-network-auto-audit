from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.aiops_alert_triage.runtime import InputRejected, handle

VALID_FINGERPRINT = "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890"


def _event(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_id": "alert-event",
        "contract_version": "1.0.0",
        "event_id": "fault-001-20260905-001",
        "source": "telemetry.equipment.sensor-12",
        "occurred_at": "2026-09-05T08:12:00+08:00",
        "severity": "critical",
        "fingerprint": VALID_FINGERPRINT,
        "summary": "BCSA 大案例 01 温度传感器越限，设备疑似过温停机",
        "grouping_keys": ["affected_system", "equipment_type"],
        "labels": {"case": "BCSA_big_01", "tenant_slug": "local-dev"},
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


def _envelope(path: Path) -> dict[str, object]:
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "aiops.alert-triage",
        "capability": "aiops.alert.triage",
        "triage": {"alert_event": _artifact(path)},
    }


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "triage-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write(root: Path, name: str, event: dict[str, object]) -> Path:
    path = root / name
    path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
    return path


def test_single_alert_event_produces_a_read_only_proposal(read_roots: Path) -> None:
    event_path = _write(read_roots, "event-01.json", _event())
    output = handle(_envelope(event_path))

    assert output["contract_id"] == "incident-proposal"
    assert output["contract_version"] == "1.0.0"
    assert output["status"] == "proposed"
    assert output["alert_fingerprint"] == VALID_FINGERPRINT
    assert len(output["proposal_id"]) == 16
    assert output["triage"]["severity"] == "critical"
    assert output["triage"]["grouping_key"].startswith("affected_system=")
    assert "BCSA_big_01" in output["triage"]["affected_system"]
    assert output["constraints"] == {
        "no_execution": True,
        "no_playbook": True,
        "requires_human_approval": True,
    }


def test_triage_is_deterministic_over_the_same_event(read_roots: Path) -> None:
    first_path = _write(read_roots, "a.json", _event())
    second_path = _write(read_roots, "b.json", _event())
    first = handle(_envelope(first_path))
    second = handle(_envelope(second_path))
    assert first["proposal_id"] == second["proposal_id"]
    assert first["triage"]["grouping_key"] == second["triage"]["grouping_key"]
    assert first["triage"]["severity"] == second["triage"]["severity"]
    assert first["triage"]["affected_system"] == second["triage"]["affected_system"]
    assert first["triage"]["summary"] == second["triage"]["summary"]
    # evidence_refs 指向各自输入文件，URI 必然不同；仅断言其分别指回本文件
    assert first["triage"]["evidence_refs"] == [first_path.resolve().as_uri()]
    assert second["triage"]["evidence_refs"] == [second_path.resolve().as_uri()]


def test_severity_is_normalized_without_execution(read_roots: Path) -> None:
    event_path = _write(read_roots, "low.json", _event(severity="low"))
    output = handle(_envelope(event_path))
    assert output["triage"]["severity"] == "low"
    assert output["constraints"]["no_playbook"] is True


def test_sha256_mismatch_is_rejected(read_roots: Path) -> None:
    event_path = _write(read_roots, "tampered.json", _event())
    artifact = _artifact(event_path)
    artifact["sha256"] = "f" * 64
    envelope = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "aiops.alert-triage",
        "capability": "aiops.alert.triage",
        "triage": {"alert_event": artifact},
    }
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_unsupported_contract_version_is_rejected(read_roots: Path) -> None:
    event_path = _write(read_roots, "v2.json", _event(contract_version="2.0.0"))
    with pytest.raises(InputRejected, match="contract version"):
        handle(_envelope(event_path))


def test_invalid_severity_is_rejected(read_roots: Path) -> None:
    event_path = _write(read_roots, "bad-sev.json", _event(severity="explosive"))
    with pytest.raises(InputRejected, match="severity"):
        handle(_envelope(event_path))


def test_path_escape_outside_read_root_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "triage-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    outside = tmp_path / "outside"
    outside.mkdir()
    event_path = _write(outside, "event.json", _event())
    with pytest.raises(InputRejected, match="outside declared read roots"):
        handle(_envelope(event_path))


def test_wrong_plugin_identity_is_rejected(read_roots: Path) -> None:
    envelope = _envelope(_write(read_roots, "event.json", _event()))
    envelope["plugin_id"] = "aiops.alert-correlation"
    with pytest.raises(InputRejected, match="plugin identity"):
        handle(envelope)
