from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.quant_experiment_evaluator.runtime import InputRejected, handle


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(tmp_path)]))


def _artifact(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "artifact_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "uri": path.resolve().as_uri(),
        "media_type": "application/json",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "classification": "restricted",
    }


def _write_report(
    tmp_path: Path,
    *,
    name: str = "report.json",
    sharpe: float = 1.7,
    total_return: float = 0.23,
    max_drawdown: float = -0.08,
    volatility: float = 0.11,
    win_rate: float = 0.62,
    period_returns: list[float] | None = None,
) -> Path:
    source = tmp_path / name
    source.write_text(
        json.dumps(
            {
                "contract_id": "backtest-report",
                "contract_version": "1.0.0",
                "backtest_id": "bt-momentum-v2",
                "strategy_key": "momentum",
                "strategy_version": "2.1.0",
                "code_sha256": "c" * 64,
                "snapshot_sha256": "d" * 64,
                "reference_time": "2026-03-31T00:00:00Z",
                "point_in_time_gate": "2026-03-31T00:00:00Z",
                "parameters": {"lookback": 20, "top_n": 10},
                "metrics": {
                    "simulated_only": True,
                    "total_return": total_return,
                    "annualized_return": 0.19,
                    "volatility": volatility,
                    "sharpe": sharpe,
                    "max_drawdown": max_drawdown,
                    "win_rate": win_rate,
                    "n_periods": 24,
                },
                "period_returns": period_returns if period_returns is not None else [0.02, 0.01, -0.005, 0.03, 0.015, 0.0, 0.025, -0.01, 0.02, 0.01, 0.005, 0.03],
                "summary": {"simulated": True, "source_refs": ["factor-pack:abc123"], "truncated": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return source


def _experiment(artifact: dict[str, object]) -> dict[str, object]:
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "quant.experiment-evaluator",
        "capability": "quant.experiment.evaluate",
        "experiment": {"report": artifact},
    }


@pytest.fixture()
def report_file(tmp_path: Path) -> Path:
    return _write_report(tmp_path)


def test_golden_report_builds_a_deterministic_experiment_evaluation(tmp_path: Path, report_file: Path) -> None:
    output = handle(_experiment(_artifact(report_file)))

    assert output["contract_id"] == "experiment-evaluation"
    assert output["strategy_key"] == "momentum"
    assert output["strategy_version"] == "2.1.0"
    assert output["status"] == "proposed"
    assert output["robustness"]["basis"] == "period_returns"
    assert output["robustness"]["overall"] in {"stable", "moderate", "fragile"}
    assert 0 <= output["robustness"]["score"] <= 1
    # No baseline -> drift unavailable and promotion must never promote.
    assert output["drift"]["available"] is False
    assert output["summary"]["baseline_included"] is False
    assert output["promotion"]["recommendation"] in {"promote", "hold", "reject"}
    assert output["experiment_id"].__len__() == 16


def test_the_evaluation_is_deterministic_for_the_same_input(tmp_path: Path, report_file: Path) -> None:
    first = handle(_experiment(_artifact(report_file)))
    second = handle(_experiment(_artifact(report_file)))
    assert first == second


def test_baseline_feeds_drift_and_promotion(tmp_path: Path, report_file: Path) -> None:
    baseline = _write_report(tmp_path, name="baseline.json", sharpe=1.4, total_return=0.18, max_drawdown=-0.10, win_rate=0.55)

    def envelope() -> dict[str, object]:
        payload = _experiment(_artifact(report_file))
        payload["experiment"]["baseline"] = _artifact(baseline)  # type: ignore[index]
        return payload

    output = handle(envelope())
    assert output["summary"]["baseline_included"] is True
    assert output["baseline_sha256"] == hashlib.sha256(baseline.read_bytes()).hexdigest()
    assert output["drift"]["available"] is True
    assert {item["metric"] for item in output["drift"]["compared_metrics"]} <= {"total_return", "annualized_return", "sharpe", "volatility", "max_drawdown", "win_rate"}
    assert output["promotion"]["recommendation"] == "promote"


def test_fragile_report_is_never_promoted(tmp_path: Path) -> None:
    source = _write_report(tmp_path, sharpe=-0.5, total_return=-0.1, max_drawdown=-0.5, volatility=0.9)
    output = handle(_experiment(_artifact(source)))
    assert output["robustness"]["overall"] == "fragile"
    assert output["promotion"]["recommendation"] == "reject"
    assert output["promotion"]["blockers"]


def test_hold_without_baseline_even_when_robust(tmp_path: Path, report_file: Path) -> None:
    output = handle(_experiment(_artifact(report_file)))
    # Without a champion baseline the recommendation stays a research hold,
    # regardless of how good the single-report robustness looks.
    assert output["promotion"]["recommendation"] == "hold"


def test_worse_challenger_is_held_and_drift_is_directional(tmp_path: Path, report_file: Path) -> None:
    baseline = _write_report(tmp_path, name="baseline.json", sharpe=2.0, total_return=0.26, max_drawdown=-0.09, win_rate=0.7)

    def envelope() -> dict[str, object]:
        payload = _experiment(_artifact(report_file))
        payload["experiment"]["baseline"] = _artifact(baseline)  # type: ignore[index]
        return payload

    output = handle(envelope())
    # Improvements (and modest drops below the threshold) are not drift; the
    # challenger simply fails to beat the champion.
    assert output["drift"]["drift_count"] == 0
    assert output["promotion"]["recommendation"] == "hold"
    assert any("Sharpe" in blocker for blocker in output["promotion"]["blockers"])


def test_worsened_risk_metrics_flag_drift(tmp_path: Path) -> None:
    challenger = _write_report(tmp_path, name="challenger.json", sharpe=1.5, max_drawdown=-0.25, volatility=0.20)
    baseline = _write_report(tmp_path, name="baseline.json", sharpe=1.5, max_drawdown=-0.10, volatility=0.10)

    def envelope() -> dict[str, object]:
        payload = _experiment(_artifact(challenger))
        payload["experiment"]["baseline"] = _artifact(baseline)  # type: ignore[index]
        return payload

    output = handle(envelope())
    drifted = {item["metric"] for item in output["drift"]["compared_metrics"] if item["drifted"]}
    assert "max_drawdown" in drifted
    assert "volatility" in drifted
    assert output["drift"]["drift_count"] >= 2


def test_sha256_mismatch_is_rejected(tmp_path: Path, report_file: Path) -> None:
    envelope = _experiment(_artifact(report_file))
    envelope["experiment"]["report"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(InputRejected, match="sha256"):
        handle(envelope)


def test_missing_metrics_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "broken.json"
    source.write_text(
        json.dumps(
            {
                "contract_id": "backtest-report",
                "contract_version": "1.0.0",
                "backtest_id": "bt-broken",
                "strategy_key": "momentum",
                "strategy_version": "2.1.0",
                "code_sha256": "c" * 64,
                "snapshot_sha256": "d" * 64,
                "reference_time": "2026-03-31T00:00:00Z",
                "point_in_time_gate": "2026-03-31T00:00:00Z",
            }
        )
    )
    with pytest.raises(InputRejected, match="metrics"):
        handle(_experiment(_artifact(source)))


def test_unexpected_capability_is_rejected(tmp_path: Path, report_file: Path) -> None:
    envelope = _experiment(_artifact(report_file))
    envelope["capability"] = "quant.factor.compute"  # type: ignore[index]
    with pytest.raises(InputRejected, match="capability"):
        handle(envelope)
