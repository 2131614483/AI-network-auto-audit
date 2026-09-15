from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.quant_simulated_backtest.runtime import InputRejected, handle

CODE_SHA256 = "ab12cd34ef567890ab12cd34ef567890ab12cd34ef567890ab12cd34ef567890"
SNAPSHOT_CSV = (
    "datetime,code,open,high,low,close,volume\n"
    "2026-09-05T09:30:00+08:00,600519,10.0,10.5,9.8,10.2,1000\n"
    "2026-09-05T09:30:00+08:00,000858,5.0,5.2,4.9,5.1,2000\n"
    "2026-09-05T09:31:00+08:00,600519,10.2,10.8,10.1,10.5,1100\n"
    "2026-09-05T09:31:00+08:00,000858,5.1,5.3,5.0,5.2,2100\n"
    "2026-09-05T09:32:00+08:00,600519,10.5,10.6,10.3,10.4,1200\n"
    "2026-09-05T09:32:00+08:00,000858,5.2,5.4,5.1,5.3,2200\n"
)


def _artifact(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "artifact_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "uri": path.resolve().as_uri(),
        "media_type": "text/csv",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "classification": "restricted",
    }


def _snapshot_ref(path: Path, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_id": "market-snapshot-ref",
        "contract_version": "1.0.0",
        "artifact": _artifact(path),
        "reference_time": "2026-09-05T15:59:00+08:00",
        "max_freshness_hours": 6,
        "point_in_time": True,
        "data_frequency": "minute",
        "expected_columns": ["datetime", "code", "open", "high", "low", "close", "volume"],
    }
    payload.update(overrides)
    return payload


def _strategy(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "strategy_key": "momentum-reversion",
        "strategy_version": "0.3.1",
        "code_sha256": CODE_SHA256,
        "parameters": {"lookback_days": 20, "top_n": 10, "tilt": 0.3},
    }
    payload.update(overrides)
    return payload


def _envelope(path: Path, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "quant.simulated-backtest",
        "capability": "quant.backtest.simulate",
        "backtest": {"snapshot_ref": _snapshot_ref(path), "strategy": _strategy()},
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "backtest-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write(root: Path, name: str, content: str = SNAPSHOT_CSV) -> Path:
    path = root / name
    path.write_text(content, encoding="utf-8")
    return path


def test_snapshot_produces_a_simulated_only_backtest_report(read_roots: Path) -> None:
    snapshot_path = _write(read_roots, "market.csv")
    output = handle(_envelope(snapshot_path))

    assert output["contract_id"] == "backtest-report"
    assert output["contract_version"] == "1.0.0"
    assert len(output["backtest_id"]) == 16
    assert output["strategy_key"] == "momentum-reversion"
    assert output["code_sha256"] == CODE_SHA256
    assert output["snapshot_sha256"] == hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert output["point_in_time_gate"] == "2026-09-05T15:59:00+08:00"
    assert output["metrics"]["simulated_only"] is True
    assert output["metrics"]["n_periods"] == 2
    assert len(output["period_returns"]) == 2
    assert output["summary"]["simulated"] is True
    assert output["summary"]["truncated"] is False
    assert output["summary"]["source_refs"] == [snapshot_path.resolve().as_uri()]


def test_backtest_is_deterministic_over_the_same_snapshot(read_roots: Path) -> None:
    first_path = _write(read_roots, "a.csv")
    second_path = _write(read_roots, "b.csv")
    first = handle(_envelope(first_path))
    second = handle(_envelope(second_path))
    assert first["backtest_id"] == second["backtest_id"]
    assert first["metrics"] == second["metrics"]
    assert first["period_returns"] == second["period_returns"]
    assert first["reference_time"] == second["reference_time"]


def test_different_strategy_changes_the_deterministic_report(read_roots: Path) -> None:
    snapshot_path = _write(read_roots, "market.csv")
    default_output = handle(_envelope(snapshot_path))
    tilted_output = handle(_envelope(snapshot_path, backtest={"snapshot_ref": _snapshot_ref(snapshot_path), "strategy": _strategy(parameters={"tilt": 0.9})}))
    assert default_output["backtest_id"] != tilted_output["backtest_id"]
    assert default_output["period_returns"] != tilted_output["period_returns"]


def test_max_periods_truncation_marks_the_report_truncated(read_roots: Path) -> None:
    snapshot_path = _write(read_roots, "market.csv")
    envelope = _envelope(snapshot_path)
    envelope["backtest"]["max_periods"] = 2  # type: ignore[index]
    output = handle(envelope)
    assert output["summary"]["truncated"] is True
    assert output["metrics"]["n_periods"] == 1


def test_too_few_periods_is_rejected(read_roots: Path) -> None:
    snapshot_path = _write(read_roots, "market.csv")
    envelope = _envelope(snapshot_path)
    envelope["backtest"]["max_periods"] = 1  # type: ignore[index]
    with pytest.raises(InputRejected, match="too few periods"):
        handle(envelope)


def test_sha256_mismatch_is_rejected(read_roots: Path) -> None:
    snapshot_path = _write(read_roots, "tampered.csv")
    artifact = _artifact(snapshot_path)
    artifact["sha256"] = "f" * 64
    envelope = _envelope(snapshot_path)
    envelope["backtest"]["snapshot_ref"]["artifact"] = artifact  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_non_point_in_time_snapshot_is_rejected(read_roots: Path) -> None:
    snapshot_path = _write(read_roots, "market.csv")
    envelope = _envelope(snapshot_path)
    envelope["backtest"]["snapshot_ref"]["point_in_time"] = False  # type: ignore[index]
    with pytest.raises(InputRejected, match="point-in-time"):
        handle(envelope)


def test_unsupported_data_frequency_is_rejected(read_roots: Path) -> None:
    snapshot_path = _write(read_roots, "market.csv")
    envelope = _envelope(snapshot_path)
    envelope["backtest"]["snapshot_ref"]["data_frequency"] = "tick"  # type: ignore[index]
    with pytest.raises(InputRejected, match="data_frequency"):
        handle(envelope)


def test_header_missing_required_column_is_rejected(read_roots: Path) -> None:
    snapshot_path = _write(read_roots, "market.csv", "datetime,code,open,high,low,volume\n1,2,3,4,5,6\n")
    with pytest.raises(InputRejected, match="header"):
        handle(_envelope(snapshot_path))


def test_path_escape_outside_read_root_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "backtest-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    outside = tmp_path / "outside"
    outside.mkdir()
    snapshot_path = _write(outside, "market.csv")
    with pytest.raises(InputRejected, match="outside declared read roots"):
        handle(_envelope(snapshot_path))


def test_wrong_plugin_identity_is_rejected(read_roots: Path) -> None:
    envelope = _envelope(_write(read_roots, "market.csv"))
    envelope["plugin_id"] = "quant.factor-compute"
    with pytest.raises(InputRejected, match="plugin identity"):
        handle(envelope)
