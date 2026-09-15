"""Isolated implementation for the verified read-only recovery-verifier plugin.

The plugin turns a baseline ``metric-series`` artifact, an observed
``metric-series`` artifact and an optional ``remediation-proposal`` artifact
into a deterministic recovery **verification ledger** entry: baseline-vs-observed
comparison, SLO/availability check and probe results, concluding
``recovered`` / ``not_recovered`` / ``insufficient_data``.

It never executes a fix, never lifts a circuit breaker and never creates a
ChangeRequest: the verification is only materialized as output, and the AIOps
domain service persists it into the immutable verification ledger after a
policy verdict.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_POINTS = 1_000_000
MAX_PROBES = 64
ERROR_METRIC_HINTS = ("error", "错误", "失败", "failure", "5xx", "exception")

SERIES_CONTRACT = "metric-series"
SERIES_VERSION = "1.0.0"
PROPOSAL_CONTRACT = "remediation-proposal"
PROPOSAL_VERSION = "1.0.0"


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


def _series_values(series: Any, label: str) -> tuple[str, str, list[float], int]:
    if not isinstance(series, dict):
        raise InputRejected(f"{label} must be a JSON object")
    if series.get("contract_id") != SERIES_CONTRACT or series.get("contract_version") != SERIES_VERSION:
        raise InputRejected(f"{label} must be a metric-series@1 artifact")
    metric = series.get("metric")
    raw_points = series.get("points")
    if not isinstance(metric, str) or not isinstance(raw_points, list):
        raise InputRejected(f"{label} must carry metric and points")
    if len(raw_points) > MAX_POINTS:
        raise InputRejected(f"{label} exceeds the fixed point budget")
    values: list[float] = []
    for raw in raw_points:
        if not isinstance(raw, dict):
            raise InputRejected(f"{label} each point must be an object")
        value = raw.get("value")
        if not isinstance(value, (int, float)):
            raise InputRejected(f"{label} each point must carry a numeric value")
        values.append(float(value))
    window = series.get("window_minutes")
    if not isinstance(window, int):
        raise InputRejected(f"{label} must carry window_minutes")
    return str(metric), str(series.get("series_id", "")), values, window


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _is_error_metric(metric: str) -> bool:
    lowered = metric.lower()
    return any(hint in lowered for hint in ERROR_METRIC_HINTS)


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "aiops.recovery-verifier":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "aiops.recovery.verify":
        raise InputRejected("unexpected capability")
    recovery_input = envelope.get("recovery")
    if not isinstance(recovery_input, dict):
        raise InputRejected("recovery input is missing")
    roots = _allowed_roots()
    baseline_path, baseline_sha256 = _read_verified(recovery_input.get("baseline"), roots, "baseline")
    observed_path, observed_sha256 = _read_verified(recovery_input.get("observed"), roots, "observed")
    proposal_sha256: str | None = None
    if recovery_input.get("proposal") is not None:
        proposal_path, proposal_sha256 = _read_verified(recovery_input.get("proposal"), roots, "proposal")
        proposal = _parse_json(proposal_path, "proposal")
        if not isinstance(proposal, dict) or proposal.get("contract_id") != PROPOSAL_CONTRACT:
            raise InputRejected("proposal must be a remediation-proposal@1 artifact")
        raw_proposals = proposal.get("proposals")
        if isinstance(raw_proposals, list) and raw_proposals:
            first = raw_proposals[0]
            if not isinstance(first, dict):
                raise InputRejected("proposal first entry must be an object")

    availability_target = recovery_input.get("availability_target")
    if not isinstance(availability_target, (int, float)) or not (0.0 <= availability_target <= 1.0):
        raise InputRejected("availability_target is outside the fixed range")
    threshold_ratio = recovery_input.get("threshold_ratio")
    if not isinstance(threshold_ratio, (int, float)) or not (0.0 <= threshold_ratio <= 10.0):
        raise InputRejected("threshold_ratio is outside the fixed range")
    raw_probes = recovery_input.get("probes")
    if raw_probes is None:
        raw_probes = []
    if not isinstance(raw_probes, list) or len(raw_probes) > MAX_PROBES:
        raise InputRejected("probes exceed the fixed budget")

    baseline_metric, _baseline_id, baseline_values, baseline_window = _series_values(_parse_json(baseline_path, "baseline"), "baseline")
    observed_metric, _observed_id, observed_values, observed_window = _series_values(_parse_json(observed_path, "observed"), "observed")
    if observed_window != baseline_window:
        raise InputRejected("baseline and observed windows must match")

    baseline_mean = _mean(baseline_values)
    observed_mean = _mean(observed_values)
    epsilon = max(abs(baseline_mean), 1e-9)
    relative_delta = abs(observed_mean - baseline_mean) / epsilon
    within_threshold = relative_delta <= threshold_ratio

    probe_results: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_probes):
        if not isinstance(raw, dict):
            raise InputRejected("each probe must be an object")
        probe = raw.get("probe")
        value = raw.get("value")
        expected = raw.get("expected")
        higher_is_better = bool(raw.get("higher_is_better", False))
        if not isinstance(probe, str) or not isinstance(value, (int, float)) or not isinstance(expected, (int, float)):
            raise InputRejected("each probe must carry probe, value and expected")
        passed = value >= expected if higher_is_better else value <= expected
        probe_results.append(
            {
                "probe": probe,
                "value": round(float(value), 6),
                "expected": round(float(expected), 6),
                "passed": passed,
                "basis": f"probe[{index + 1}] {'lower-is-better' if not higher_is_better else 'higher-is-better'}",
            }
        )

    probes_checked = len(probe_results)
    probes_passed = sum(1 for probe in probe_results if probe["passed"])
    error_rate_observed = observed_mean if _is_error_metric(observed_metric) else 0.0
    observed_availability = round(1.0 - min(1.0, max(0.0, error_rate_observed)), 6)
    error_budget_remaining = (
        round(max(0.0, min(1.0, (availability_target - error_rate_observed) / availability_target)), 6)
        if availability_target > 0
        else 1.0
    )
    slo_met = observed_availability >= availability_target
    all_probes_passed = probes_checked == 0 or probes_passed == probes_checked

    if not baseline_values or not observed_values:
        status = "insufficient_data"
    elif within_threshold and slo_met and all_probes_passed:
        status = "recovered"
    else:
        status = "not_recovered"

    verification_id = hashlib.sha256(
        f"{baseline_sha256}|{observed_sha256}|{proposal_sha256 or ''}|{availability_target}|{threshold_ratio}".encode("utf-8")
    ).hexdigest()[:16]

    evidence_refs = [baseline_sha256, observed_sha256]
    if proposal_sha256:
        evidence_refs.append(proposal_sha256)

    return {
        "contract_id": "recovery-verification",
        "contract_version": "1.0.0",
        "verification_id": verification_id,
        "baseline_sha256": baseline_sha256,
        "observed_sha256": observed_sha256,
        **({"proposal_sha256": proposal_sha256} if proposal_sha256 else {}),
        "status": status,
        "reference_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "baseline_points": len(baseline_values),
            "observed_points": len(observed_values),
            "probes_checked": probes_checked,
            "probes_passed": probes_passed,
            "verdict": status,
            "truncated": False,
        },
        "comparison": {
            "baseline_mean": round(baseline_mean, 6),
            "observed_mean": round(observed_mean, 6),
            "relative_delta": round(relative_delta, 6),
            "threshold_ratio": round(float(threshold_ratio), 6),
            "within_threshold": within_threshold,
            "metrics": [
                {
                    "metric": observed_metric,
                    "baseline": round(baseline_mean, 6),
                    "observed": round(observed_mean, 6),
                    "delta": round(observed_mean - baseline_mean, 6),
                    "within_threshold": within_threshold,
                }
            ],
        },
        "slo": {
            "availability_target": round(float(availability_target), 6),
            "observed_availability": observed_availability,
            "error_budget_remaining": error_budget_remaining,
            "met": slo_met,
            "probes": probe_results,
        },
        "evidence_refs": evidence_refs,
        "constraints": {
            "no_execution": True,
            "no_circuit_breaker_change": True,
            "no_change_request": True,
            "ledger_immutable": True,
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
