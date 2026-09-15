"""Isolated implementation for the verified read-only experiment-evaluator plugin.

The plugin turns a ``backtest-report`` artifact (optionally paired with a
baseline champion report) into a deterministic experiment-evaluation **draft**:
robustness computed from the challenger's period returns / summary metrics,
drift compared against the baseline when present, and a promotion
recommendation (``promote``/``hold``/``reject``) with blockers.

It never updates a production model and never generates orders: the draft is
only materialized as output, and the quant domain service stages it into a
research proposal after a policy verdict.  Promotion requires independent
review or human confirmation.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_PERIOD_RETURNS = 100_000
MAX_EVIDENCE_REFS = 64
RELATIVE_DRIFT_THRESHOLD = 0.2
_MIN_PROMOTE_ROBUSTNESS = 0.45


class InputRejected(ValueError):
    """The parent passed an input outside the fixed read-only contract."""


def _allowed_roots() -> tuple[Path, ...]:
    try:
        raw_roots = json.loads(os.environ["AUDIT_PLUGIN_READ_ROOTS"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise InputRejected("read roots are unavailable") from exc
    if not isinstance(raw_roots, list) or not raw_roots:
        raise InputRejected("read roots are invalid")
    return tuple(Path(str(raw)).resolve() for raw in raw_roots)


def _resolve_file(uri: object, roots: tuple[Path, ...]) -> Path:
    if not isinstance(uri, str):
        raise InputRejected("artifact URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("artifact URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("artifact file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("artifact is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("artifact is outside declared read roots") from exc
    return resolved


def _read_verified(artifact: Any, roots: tuple[Path, ...], label: str) -> tuple[Path, str]:
    if not isinstance(artifact, dict):
        raise InputRejected(f"{label} artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), roots)
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected(f"{label} exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected(f"{label} size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected(f"{label} sha256 does not match reference")
    return path, actual_hash


def _parse_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejected(f"{label} must be UTF-8 JSON") from exc


def _stable_hash(text: str, size: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:size]


def _bounded(items: list[str], limit: int) -> list[str]:
    return items[:limit]


def _robustness(metrics: dict[str, Any], period_returns: list[float]) -> dict[str, Any]:
    """Deterministic robustness profile of a single backtest report."""
    sharpe = metrics.get("sharpe")
    sharpe = float(sharpe) if isinstance(sharpe, (int, float)) else 0.0
    max_drawdown = metrics.get("max_drawdown")
    max_drawdown = float(max_drawdown) if isinstance(max_drawdown, (int, float)) else 0.0
    volatility = metrics.get("volatility")
    volatility = abs(float(volatility)) if isinstance(volatility, (int, float)) else 0.0

    def _sharpe_factor(value: float) -> float:
        if value >= 2.0:
            return 1.0
        if value >= 1.0:
            return 0.7
        if value >= 0.0:
            return 0.4
        return 0.1

    def _drawdown_factor(value: float) -> float:
        depth = abs(min(value, 0.0))
        if depth <= 0.10:
            return 1.0
        if depth <= 0.25:
            return 0.6
        if depth <= 0.40:
            return 0.3
        return 0.1

    notes: list[str] = []
    if period_returns:
        n = len(period_returns)
        mean = sum(period_returns) / n
        variance = sum((value - mean) ** 2 for value in period_returns) / n
        stdev = variance ** 0.5
        positive_ratio = sum(1 for value in period_returns if value > 0) / n
        if mean > 0:
            cv = stdev / mean
            if cv <= 0.5:
                stability = 1.0
            elif cv <= 1.0:
                stability = 0.6
            else:
                stability = 0.3
        else:
            stability = 0.0
        score = round(0.35 * _sharpe_factor(sharpe) + 0.35 * stability + 0.15 * positive_ratio + 0.15 * _drawdown_factor(max_drawdown), 4)
        overall = "stable" if score >= 0.7 else "moderate" if score >= 0.45 else "fragile"
        notes.append(f"基于 {n} 个逐期收益计算稳健性。")
        return {
            "overall": overall,
            "score": score,
            "basis": "period_returns",
            "period_return_stability": round(stability, 4),
            "positive_period_ratio": round(positive_ratio, 4),
            "max_drawdown": round(max_drawdown, 6),
            "volatility": round(volatility, 6),
            "notes": notes,
        }
    score = round(0.4 * _sharpe_factor(sharpe) + 0.35 * _drawdown_factor(max_drawdown) + 0.25 * (1.0 if volatility <= 0.05 else 0.6 if volatility <= 0.15 else 0.3), 4)
    overall = "stable" if score >= 0.7 else "moderate" if score >= 0.45 else "fragile"
    notes.append("报告未提供逐期收益，稳健性基于汇总指标估算。")
    return {
        "overall": overall,
        "score": score,
        "basis": "summary_metrics",
        "period_return_stability": None,
        "positive_period_ratio": None,
        "max_drawdown": round(max_drawdown, 6),
        "volatility": round(volatility, 6),
        "notes": notes,
    }


_COMPARED_METRICS = ("total_return", "annualized_return", "sharpe", "volatility", "max_drawdown", "win_rate")
# Higher-is-better metrics flag drift when they drop by the relative threshold;
# lower-is-better metrics (volatility, max_drawdown) flag drift when they worsen.
_HIGHER_IS_BETTER = frozenset({"total_return", "annualized_return", "sharpe", "win_rate"})


def _drift(challenger: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Deterministic per-metric drift of the challenger against the champion.

    Drift is directional: a metric counts as drifted only when the challenger
    meaningfully worsened relative to the baseline (a promotion that improves
    return/Sharpe is not drift).
    """
    compared: list[dict[str, Any]] = []
    for metric in _COMPARED_METRICS:
        left = challenger.get(metric)
        right = baseline.get(metric)
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            continue
        left, right = float(left), float(right)
        absolute_delta = round(left - right, 8)
        if right != 0:
            relative_delta = round(absolute_delta / abs(right), 8)
        else:
            relative_delta = None
        if relative_delta is None:
            drifted = False
        elif metric in _HIGHER_IS_BETTER:
            drifted = relative_delta <= -RELATIVE_DRIFT_THRESHOLD
        else:
            # Lower-is-better (volatility, drawdown depth): drift when the
            # challenger magnitude grew by the relative threshold, regardless
            # of sign convention (volatility is positive, drawdown negative).
            drifted = abs(left) >= abs(right) * (1 + RELATIVE_DRIFT_THRESHOLD) if right != 0 else False
        compared.append(
            {
                "metric": metric,
                "challenger": left,
                "baseline": right,
                "absolute_delta": absolute_delta,
                "relative_delta": relative_delta,
                "drifted": drifted,
                "threshold": RELATIVE_DRIFT_THRESHOLD,
            }
        )
    drift_count = sum(1 for item in compared if item["drifted"])
    worst = max((item for item in compared if item["drifted"]), key=lambda item: abs(item["relative_delta"] or 0.0), default=None)
    return {
        "available": bool(compared),
        "drift_count": drift_count,
        "worst_metric": worst["metric"] if worst else None,
        "compared_metrics": compared,
    }


def _promotion(robustness: dict[str, Any], drift: dict[str, Any], challenger: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    """Deterministic promotion recommendation; never promotes without robustness."""
    blockers: list[str] = []
    if robustness["score"] < _MIN_PROMOTE_ROBUSTNESS:
        blockers.append(f"稳健性得分 {robustness['score']} 低于晋级门槛 {_MIN_PROMOTE_ROBUSTNESS}。")
    if robustness["overall"] == "fragile":
        blockers.append("稳健性评级为 fragile。")
    if baseline is None:
        if blockers:
            recommendation = "reject"
        else:
            recommendation = "hold"
        rationale = "未提供基线冠军报告，无法对比漂移；实验保持研究提案。"
        return {"recommendation": recommendation, "rationale": rationale, "blockers": blockers}

    challenger_sharpe = challenger.get("sharpe")
    baseline_sharpe = baseline.get("sharpe")
    challenger_drawdown = challenger.get("max_drawdown")
    baseline_drawdown = baseline.get("max_drawdown")
    if isinstance(challenger_sharpe, (int, float)) and isinstance(baseline_sharpe, (int, float)):
        if float(challenger_sharpe) < float(baseline_sharpe):
            blockers.append("挑战者 Sharpe 落后基线冠军。")
    if isinstance(challenger_drawdown, (int, float)) and isinstance(baseline_drawdown, (int, float)):
        if float(challenger_drawdown) < float(baseline_drawdown) * 1.05:
            blockers.append("挑战者回撤不优于基线。")
    if drift["drift_count"] > 1:
        blockers.append(f"对比基线的漂移指标达 {drift['drift_count']} 项。")

    if blockers:
        recommendation = "reject" if robustness["score"] < _MIN_PROMOTE_ROBUSTNESS else "hold"
        rationale = "晋级前置条件未满足，实验保持研究提案。" if recommendation == "hold" else "稳健性不足，建议拒绝晋级并复核实验设计。"
        return {"recommendation": recommendation, "rationale": rationale, "blockers": blockers}

    recommendation = "promote"
    rationale = "挑战者在稳健性与风险调整收益上满足晋级门槛，可进入独立 Reviewer 复核。"
    return {"recommendation": recommendation, "rationale": rationale, "blockers": blockers}


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "quant.experiment-evaluator":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "quant.experiment.evaluate":
        raise InputRejected("unexpected capability")
    experiment_input = envelope.get("experiment")
    if not isinstance(experiment_input, dict):
        raise InputRejected("experiment input is missing")
    roots = _allowed_roots()
    report_path, report_sha256 = _read_verified(experiment_input.get("report"), roots, "backtest report")
    baseline_path: Path | None = None
    baseline_sha256: str | None = None
    if experiment_input.get("baseline") is not None:
        baseline_path, baseline_sha256 = _read_verified(experiment_input.get("baseline"), roots, "baseline report")

    report = _parse_json(report_path, "backtest report")
    if not isinstance(report, dict):
        raise InputRejected("backtest report must be a JSON object")
    strategy_key = report.get("strategy_key")
    strategy_version = report.get("strategy_version")
    snapshot_sha256 = report.get("snapshot_sha256")
    reference_time = report.get("reference_time")
    if not isinstance(strategy_key, str) or not isinstance(strategy_version, str) or not isinstance(snapshot_sha256, str) or not isinstance(reference_time, str):
        raise InputRejected("backtest report must carry strategy identity and snapshot reference")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise InputRejected("backtest report must carry metrics")
    raw_period_returns = report.get("period_returns")
    if raw_period_returns is None:
        raw_period_returns = []
    if not isinstance(raw_period_returns, list) or len(raw_period_returns) > MAX_PERIOD_RETURNS:
        raise InputRejected("period_returns exceed the fixed budget")
    period_returns = [float(value) for value in raw_period_returns if isinstance(value, (int, float))]

    baseline: dict[str, Any] | None = None
    if baseline_path is not None:
        baseline = _parse_json(baseline_path, "baseline report")
        if not isinstance(baseline, dict):
            raise InputRejected("baseline report must be a JSON object")
        if not isinstance(baseline.get("metrics"), dict):
            raise InputRejected("baseline report must carry metrics")

    robustness = _robustness(metrics, period_returns)
    drift = _drift(metrics, baseline["metrics"] if baseline else {})
    promotion = _promotion(robustness, drift, metrics, baseline["metrics"] if baseline else None)

    evidence_refs: list[str] = []
    source_refs = report.get("summary", {}).get("source_refs")
    if isinstance(source_refs, list):
        evidence_refs.extend(str(value) for value in source_refs if isinstance(value, str))
    evidence_refs.append(snapshot_sha256)

    return {
        "contract_id": "experiment-evaluation",
        "contract_version": "1.0.0",
        "experiment_id": _stable_hash(f"{report_sha256}|{baseline_sha256 or ''}"),
        "report_sha256": report_sha256,
        **({"baseline_sha256": baseline_sha256} if baseline_sha256 else {}),
        "strategy_key": strategy_key,
        "strategy_version": strategy_version,
        "snapshot_sha256": snapshot_sha256,
        "reference_time": reference_time,
        "status": "proposed",
        "robustness": robustness,
        "drift": drift,
        "promotion": promotion,
        "evidence_refs": _bounded(sorted(set(evidence_refs)), MAX_EVIDENCE_REFS),
        "summary": {
            "report_sha256": report_sha256,
            "baseline_included": baseline is not None,
            "recommendation": promotion["recommendation"],
            "truncated": len(raw_period_returns) > len(period_returns) if isinstance(raw_period_returns, list) else False,
        },
    }


def main() -> int:
    try:
        envelope = json.loads(sys.stdin.read())
        if not isinstance(envelope, dict):
            raise InputRejected("child envelope must be an object")
        output = handle(envelope)
        sys.stdout.buffer.write(json.dumps({"ok": True, "output": output}, ensure_ascii=False).encode("utf-8"))
        return 0
    except (InputRejected, json.JSONDecodeError) as exc:
        sys.stdout.buffer.write(
            json.dumps({"ok": False, "error": {"code": "invalid_input", "message": str(exc)}}, ensure_ascii=False).encode("utf-8")
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
