from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.aiops_alert_correlation.runtime import InputRejected, handle


def _envelope(path: Path, *, window_minutes: int = 60, max_candidates: int = 100, grouping_keys: list[str] | None = None) -> dict[str, object]:
    content = path.read_bytes()
    alerts: dict[str, object] = {
        "artifact": {
            "artifact_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "uri": path.resolve().as_uri(),
            "media_type": "application/json",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "classification": "internal",
        },
        "window_minutes": window_minutes,
        "max_candidates": max_candidates,
    }
    if grouping_keys is not None:
        alerts["grouping_keys"] = grouping_keys
    return {"protocol": "audit-network-plugin-child-v1", "plugin_id": "aiops.alert-correlation", "capability": "aiops.alert.correlate", "alerts": alerts}


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "alerts-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write(root: Path, name: str, events: list[dict[str, object]]) -> Path:
    path = root / name
    path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    return path


def _alert(ticket: str, timestamp: str, severity: str = "medium", **extra: object) -> dict[str, object]:
    return {"ticket_id": ticket, "timestamp": timestamp, "severity": severity, **extra}


def test_single_alert_produces_one_candidate(read_roots: Path) -> None:
    path = _write(read_roots, "one.json", [_alert("FT_001", "2025-08-18T15:28:50.376773", affected_system="溶剂浓度")])
    output = handle(_envelope(path))
    assert output["contract_id"] == "incident-candidate-set"
    assert output["summary"]["alerts_processed"] == 1
    assert output["summary"]["candidate_count"] == 1
    assert output["summary"]["truncated"] is False
    candidate = output["candidates"][0]
    assert candidate["alert_refs"] == ["FT_001"]
    assert candidate["count"] == 1
    assert candidate["correlation_key"] == "affected_system=溶剂浓度"


def test_alerts_outside_window_are_separate_incidents(read_roots: Path) -> None:
    path = _write(
        read_roots,
        "split.json",
        [
            _alert("FT_001", "2025-08-18T15:28:50", affected_system="溶剂浓度"),
            _alert("FT_002", "2025-08-18T17:00:00", affected_system="溶剂浓度"),
        ],
    )
    output = handle(_envelope(path, window_minutes=30))
    assert output["summary"]["candidate_count"] == 2
    assert {c["incident_id"] for c in output["candidates"]} == {"INC-0001", "INC-0002"}


def test_alerts_inside_window_merge_and_escalate_severity(read_roots: Path) -> None:
    path = _write(
        read_roots,
        "merge.json",
        [
            _alert("FT_001", "2025-08-18T15:28:50", severity="低", affected_system="溶剂浓度"),
            _alert("FT_002", "2025-08-18T15:45:00", severity="高", affected_system="溶剂浓度"),
        ],
    )
    output = handle(_envelope(path, window_minutes=60))
    assert output["summary"]["candidate_count"] == 1
    candidate = output["candidates"][0]
    assert candidate["count"] == 2
    assert candidate["severity"] == "high"
    assert candidate["alert_refs"] == ["FT_001", "FT_002"]


def test_grouping_keys_split_events_into_distinct_incidents(read_roots: Path) -> None:
    path = _write(
        read_roots,
        "grouped.json",
        [
            _alert("FT_001", "2025-08-18T15:28:50", affected_system="溶剂浓度", equipment_type="Type-II"),
            _alert("FT_002", "2025-08-18T15:35:00", affected_system="环境湿度", equipment_type="Type-II"),
        ],
    )
    output = handle(_envelope(path, window_minutes=60, grouping_keys=["affected_system", "equipment_type"]))
    assert output["summary"]["candidate_count"] == 2
    keys = {c["correlation_key"] for c in output["candidates"]}
    assert "affected_system=溶剂浓度|equipment_type=Type-II" in keys
    assert "affected_system=环境湿度|equipment_type=Type-II" in keys


def test_duplicate_request_with_same_immutable_input_is_idempotent(read_roots: Path) -> None:
    path = _write(read_roots, "idem.json", [_alert("FT_001", "2025-08-18T15:28:50", affected_system="溶剂浓度")])
    first = handle(_envelope(path))
    second = handle(_envelope(path))
    assert first["alert_set_sha256"] == second["alert_set_sha256"]
    assert first["candidates"] == second["candidates"]


def test_tampered_sha256_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "tampered.json", [_alert("FT_001", "2025-08-18T15:28:50", affected_system="溶剂浓度")])
    envelope = _envelope(path)
    envelope["alerts"]["artifact"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_size_mismatch_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "sized.json", [_alert("FT_001", "2025-08-18T15:28:50", affected_system="溶剂浓度")])
    envelope = _envelope(path)
    envelope["alerts"]["artifact"]["size_bytes"] = 1  # type: ignore[index]
    with pytest.raises(InputRejected, match="size"):
        handle(envelope)


def test_missing_timestamp_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "no-ts.json", [{"ticket_id": "FT_001", "severity": "medium", "affected_system": "溶剂浓度"}])
    with pytest.raises(InputRejected, match="timestamp"):
        handle(_envelope(path))


def test_non_json_artifact_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "bad.json", [])
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(InputRejected, match="UTF-8 JSON"):
        handle(_envelope(path))


def test_window_minutes_outside_budget_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "window.json", [_alert("FT_001", "2025-08-18T15:28:50", affected_system="溶剂浓度")])
    with pytest.raises(InputRejected, match="window_minutes"):
        handle(_envelope(path, window_minutes=0))


def test_artifact_outside_declared_read_roots_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(tmp_path / "allowed")]))
    outside = tmp_path / "alerts-outside.json"
    outside.write_text(json.dumps([_alert("FT_001", "2025-08-18T15:28:50")]), encoding="utf-8")
    with pytest.raises(InputRejected, match="read roots"):
        handle(_envelope(outside))


def test_environment_without_read_roots_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AUDIT_PLUGIN_READ_ROOTS", raising=False)
    path = tmp_path / "no-env.json"
    path.write_text(json.dumps([_alert("FT_001", "2025-08-18T15:28:50")]), encoding="utf-8")
    with pytest.raises(InputRejected, match="read roots"):
        handle(_envelope(path))


def test_candidate_cap_marks_summary_truncated(read_roots: Path) -> None:
    events = []
    for i in range(25):
        events.append(_alert(f"FT_{i:03d}", f"2025-08-18T15:{i:02d}:00", affected_system=f"系统{i:02d}"))
    path = _write(read_roots, "many.json", events)
    output = handle(_envelope(path, max_candidates=10))
    assert output["summary"]["candidate_count"] == 10
    assert output["summary"]["truncated"] is True


def test_chinese_severity_aliases_are_normalized(read_roots: Path) -> None:
    path = _write(
        read_roots,
        "aliases.json",
        [
            _alert("FT_001", "2025-08-18T15:28:50", severity="严重", affected_system="溶剂浓度"),
            _alert("FT_002", "2025-08-18T15:35:00", severity="紧急", affected_system="溶剂浓度"),
        ],
    )
    output = handle(_envelope(path, window_minutes=60))
    assert output["candidates"][0]["severity"] == "high"
