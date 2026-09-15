from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.quant_snapshot_guard.runtime import InputRejected, handle


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(tmp_path)]))


def _snapshot_envelope(
    path: Path,
    *,
    reference_time: str = "2026-08-19T15:00:00",
    max_freshness_hours: float = 24,
    point_in_time: bool = True,
) -> dict[str, object]:
    content = path.read_bytes()
    snapshot: dict[str, object] = {
        "artifact": {
            "artifact_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "uri": path.resolve().as_uri(),
            "media_type": "text/csv",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "classification": "restricted",
        },
        "reference_time": reference_time,
        "max_freshness_hours": max_freshness_hours,
        "point_in_time": point_in_time,
        "data_frequency": "minute",
    }
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "quant.snapshot-guard",
        "capability": "quant.dataset.validate",
        "snapshot": snapshot,
    }


def test_golden_snapshot_passes_all_gates(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text(
        "timestamp,price,volume\n"
        "2026-08-19 12:00:00,100.0,1000\n"
        "2026-08-19 12:01:00,101.0,1200\n"
        "2026-08-19 12:02:00,102.0,800\n",
        encoding="utf-8",
    )
    output = handle(_snapshot_envelope(source))
    assert output["contract_id"] == "dataset-validation"
    assert output["summary"]["valid"] is True
    assert output["summary"]["checked_rows"] == 3
    assert output["summary"]["duplicate_timestamps"] == 0
    assert output["violations"] == []


def test_stale_snapshot_fails_freshness_gate(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text(
        "timestamp,price,volume\n"
        "2026-08-01 12:00:00,100.0,1000\n",
        encoding="utf-8",
    )
    output = handle(_snapshot_envelope(source))
    assert output["summary"]["valid"] is False
    assert any(check["check_id"] == "freshness" and check["status"] == "fail" for check in output["checks"])


def test_future_timestamp_fails_point_in_time_gate(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text(
        "timestamp,price,volume\n"
        "2026-09-01 12:00:00,100.0,1000\n",
        encoding="utf-8",
    )
    output = handle(_snapshot_envelope(source))
    assert output["summary"]["valid"] is False
    assert any(check["check_id"] == "point_in_time" and check["status"] == "fail" for check in output["checks"])


def test_duplicate_timestamps_are_reported(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text(
        "timestamp,price,volume\n"
        "2026-08-19 12:00:00,100.0,1000\n"
        "2026-08-19 12:00:00,101.0,1200\n",
        encoding="utf-8",
    )
    output = handle(_snapshot_envelope(source))
    assert output["summary"]["duplicate_timestamps"] == 1
    assert any(check["check_id"] == "duplicate_timestamps" and check["status"] == "warn" for check in output["checks"])


def test_missing_expected_columns_fail_column_gate(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text(
        "timestamp,price\n"
        "2026-08-19 12:00:00,100.0\n",
        encoding="utf-8",
    )
    envelope = _snapshot_envelope(source)
    envelope["snapshot"]["expected_columns"] = ["timestamp", "volume"]  # type: ignore[index]
    output = handle(envelope)
    assert output["summary"]["valid"] is False
    assert any(check["check_id"] == "columns" and check["status"] == "fail" for check in output["checks"])


def test_sha256_mismatch_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text("timestamp,price\n2026-08-19 12:00:00,100.0\n", encoding="utf-8")
    envelope = _snapshot_envelope(source)
    envelope["snapshot"]["artifact"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)
