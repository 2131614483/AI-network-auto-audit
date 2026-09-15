from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.quant_factor_compute.runtime import InputRejected, handle


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(tmp_path)]))


def _factor_envelope(path: Path, *, factors: list[str] | None = None, lookback: int = 3) -> dict[str, object]:
    content = path.read_bytes()
    dataset: dict[str, object] = {
        "artifact": {
            "artifact_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "uri": path.resolve().as_uri(),
            "media_type": "text/csv",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "classification": "restricted",
        },
        "reference_time": "2026-08-19T15:00:00",
        "factors": factors or ["returns", "volatility", "momentum", "zscore", "drawdown"],
        "lookback": lookback,
    }
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "quant.factor-compute",
        "capability": "quant.factor.compute",
        "dataset": dataset,
    }


def test_golden_snapshot_computes_expected_factors(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text(
        "timestamp,price,volume\n"
        "2026-08-19 12:00:00,100.0,1000\n"
        "2026-08-19 12:01:00,110.0,2000\n"
        "2026-08-19 12:02:00,121.0,3000\n",
        encoding="utf-8",
    )
    output = handle(_factor_envelope(source))
    assert output["contract_id"] == "factor-artifact-ref"
    factor_ids = {factor["factor_id"] for factor in output["factors"]}
    assert factor_ids == {"returns", "volatility", "momentum", "zscore", "drawdown"}
    by_id = {factor["factor_id"]: factor for factor in output["factors"]}
    assert by_id["returns"]["value"] == pytest.approx(121.0 / 110.0 - 1, abs=1e-8)
    assert by_id["momentum"]["value"] == pytest.approx(121.0 / 100.0 - 1, abs=1e-8)
    assert by_id["drawdown"]["value"] <= 0
    assert output["summary"]["series_points"] == 3


def test_volume_zscore_requires_volume_column(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text(
        "timestamp,price,volume\n"
        "2026-08-19 12:00:00,100.0,1000\n"
        "2026-08-19 12:01:00,110.0,2000\n",
        encoding="utf-8",
    )
    output = handle(_factor_envelope(source, factors=["volume_zscore"]))
    assert output["factors"][0]["factor_id"] == "volume_zscore"
    assert output["factors"][0]["n"] == 2


def test_missing_volume_column_rejects_volume_zscore(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text(
        "timestamp,price\n"
        "2026-08-19 12:00:00,100.0\n"
        "2026-08-19 12:01:00,110.0\n",
        encoding="utf-8",
    )
    with pytest.raises(InputRejected, match="volume"):
        handle(_factor_envelope(source, factors=["volume_zscore"]))


def test_sha256_mismatch_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text("timestamp,price\n2026-08-19 12:00:00,100.0\n2026-08-19 12:01:00,110.0\n", encoding="utf-8")
    envelope = _factor_envelope(source)
    envelope["dataset"]["artifact"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_unsupported_factor_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.csv"
    source.write_text("timestamp,price\n2026-08-19 12:00:00,100.0\n2026-08-19 12:01:00,110.0\n", encoding="utf-8")
    with pytest.raises(InputRejected, match="unsupported factor"):
        handle(_factor_envelope(source, factors=["alpha_harvest"]))
