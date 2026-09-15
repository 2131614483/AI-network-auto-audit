"""Isolated implementation for the verified read-only simulated-backtest plugin.

The plugin turns a ``market-snapshot-ref`` CSV artifact plus a declared
strategy declaration into a deterministic simulated backtest report.  It only
computes a read-only projection on the frozen point-in-time snapshot: it never
generates an order, never contacts a broker and never writes a dataset.  Every
metric is produced from the snapshot's close prices and a PRNG seeded by the
strategy and snapshot hashes, so identical inputs always yield an identical
report and the report can never be mistaken for live trading output.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from math import prod, sqrt
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_ROWS = 1_000_000
MAX_PERIODS = 100_000
DEFAULT_COLUMNS = ("datetime", "code", "open", "high", "low", "close", "volume")
DATA_FREQUENCIES = frozenset({"minute", "hour", "day"})
PERIODS_PER_YEAR = {"minute": 252 * 390, "hour": 252 * 6, "day": 252}
TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
)


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
        raise InputRejected("snapshot URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("snapshot URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("snapshot file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("snapshot is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("snapshot is outside declared read roots") from exc
    return resolved


def _read_verified_snapshot(artifact: Any, roots: tuple[Path, ...]) -> tuple[bytes, str]:
    if not isinstance(artifact, dict):
        raise InputRejected("snapshot artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), roots)
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("snapshot exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("snapshot size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("snapshot sha256 does not match reference")
    return content, actual_hash


def _parse_datetime(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    for fmt in TIMESTAMP_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validated_columns(raw_columns: Any) -> tuple[str, ...]:
    if raw_columns is None:
        return DEFAULT_COLUMNS
    if not isinstance(raw_columns, list) or not raw_columns or len(raw_columns) > 64:
        raise InputRejected("snapshot expected_columns are outside the fixed budget")
    columns: list[str] = []
    for raw in raw_columns:
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 255:
            raise InputRejected("snapshot expected_columns are invalid")
        column = raw.strip()
        if column in columns:
            raise InputRejected("snapshot expected_columns are duplicated")
        columns.append(column)
    return tuple(columns)


def _validated_parameters(raw_parameters: Any) -> dict[str, Any]:
    if not isinstance(raw_parameters, dict):
        raise InputRejected("strategy parameters must be an object")
    if len(raw_parameters) > 32:
        raise InputRejected("strategy parameters exceed the fixed key budget")
    serialized = json.dumps(raw_parameters, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    if len(serialized) > 4096:
        raise InputRejected("strategy parameters exceed the fixed size budget")
    return raw_parameters


def _parse_rows(content: bytes, expected_columns: tuple[str, ...]) -> list[tuple[datetime, str, float]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("snapshot must be UTF-8 text") from exc
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise InputRejected("snapshot header is missing") from exc
    if not header:
        raise InputRejected("snapshot header is empty")
    header = [cell.strip() for cell in header]
    required = set(expected_columns)
    missing = required - set(header)
    if missing or "datetime" not in header or "close" not in header:
        raise InputRejected("snapshot header misses a required column")
    if len(set(header)) != len(header):
        raise InputRejected("snapshot header is duplicated")
    try:
        datetime_index = header.index("datetime")
        close_index = header.index("close")
    except ValueError as exc:
        raise InputRejected("snapshot header misses datetime/close") from exc
    code_index = header.index("code") if "code" in header else None

    rows: list[tuple[datetime, str, float]] = []
    seen: set[tuple[datetime, str]] = set()
    for line_number, row in enumerate(reader, start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(rows) >= MAX_ROWS:
            raise InputRejected("snapshot exceeds the fixed row budget")
        if len(row) < len(header):
            raise InputRejected(f"snapshot row {line_number} has too few fields")
        occurred_at = _parse_datetime(row[datetime_index])
        if occurred_at is None:
            raise InputRejected(f"snapshot row {line_number} datetime is invalid")
        try:
            close = float(row[close_index])
        except (TypeError, ValueError) as exc:
            raise InputRejected(f"snapshot row {line_number} close is not numeric") from exc
        code = row[code_index].strip() if code_index is not None else "UNIV"
        if not code:
            raise InputRejected(f"snapshot row {line_number} code is empty")
        key = (occurred_at, code)
        if key in seen:
            raise InputRejected(f"snapshot row {line_number} duplicates a datetime/code pair")
        seen.add(key)
        rows.append((occurred_at, code, close))
    return rows


def _metrics(returns: list[float], periods_per_year: int) -> dict[str, float | int]:
    count = len(returns)
    gross = [1.0 + value for value in returns]
    total_return = prod(gross) - 1.0
    mean = fmean(returns)
    variance = sum((value - mean) ** 2 for value in returns) / count
    std = sqrt(variance)
    if 1.0 + total_return > 0.0:
        # Annualize in log space and cap at +100x so a very short window can
        # never overflow the extrapolation.
        annualized_return = math.exp(min(math.log1p(total_return) * periods_per_year / count, math.log(101.0))) - 1.0
    else:
        annualized_return = -1.0
    volatility = std * sqrt(periods_per_year)
    sharpe = mean / std * sqrt(periods_per_year) if std > 1e-12 else 0.0
    peak = 1.0
    cumulative = 1.0
    max_drawdown = 0.0
    for value in returns:
        cumulative *= 1.0 + value
        if cumulative > peak:
            peak = cumulative
        drawdown = cumulative / peak - 1.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
    win_rate = sum(1 for value in returns if value > 0.0) / count
    return {
        "simulated_only": True,
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized_return, 6),
        "volatility": round(volatility, 6),
        "sharpe": round(sharpe, 6),
        "max_drawdown": round(max_drawdown, 6),
        "win_rate": round(win_rate, 6),
        "n_periods": count,
    }


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "quant.simulated-backtest":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "quant.backtest.simulate":
        raise InputRejected("unexpected capability")
    backtest_input = envelope.get("backtest")
    if not isinstance(backtest_input, dict):
        raise InputRejected("simulated backtest input is missing")
    snapshot_ref = backtest_input.get("snapshot_ref")
    strategy = backtest_input.get("strategy")
    if not isinstance(snapshot_ref, dict) or not isinstance(strategy, dict):
        raise InputRejected("snapshot reference or strategy declaration is missing")

    reference_time_raw = snapshot_ref.get("reference_time")
    gate = _parse_datetime(reference_time_raw)
    if gate is None:
        raise InputRejected("snapshot reference_time must be a parseable date-time")
    max_freshness_hours = snapshot_ref.get("max_freshness_hours")
    if not isinstance(max_freshness_hours, int) or isinstance(max_freshness_hours, bool) or not 1 <= max_freshness_hours <= 8760:
        raise InputRejected("snapshot max_freshness_hours is outside the fixed range")
    if snapshot_ref.get("point_in_time") is not True:
        raise InputRejected("snapshot must be a point-in-time gate")
    data_frequency = snapshot_ref.get("data_frequency")
    if data_frequency not in DATA_FREQUENCIES:
        raise InputRejected("snapshot data_frequency is outside the fixed enum")
    expected_columns = _validated_columns(snapshot_ref.get("expected_columns"))

    strategy_key = strategy.get("strategy_key")
    strategy_version = strategy.get("strategy_version")
    code_sha256 = strategy.get("code_sha256")
    if not isinstance(strategy_key, str) or not strategy_key.strip() or len(strategy_key) > 255:
        raise InputRejected("strategy_key is invalid")
    if not isinstance(strategy_version, str) or len(strategy_version) > 64:
        raise InputRejected("strategy_version is invalid")
    if not isinstance(code_sha256, str) or len(code_sha256) != 64:
        raise InputRejected("strategy code_sha256 must be a 64-char sha256")
    code_sha256 = code_sha256.lower()
    parameters = _validated_parameters(strategy.get("parameters"))
    tilt_raw = parameters.get("tilt", 0.3)
    if isinstance(tilt_raw, bool) or not isinstance(tilt_raw, (int, float)) or not 0.0 <= float(tilt_raw) <= 2.0:
        raise InputRejected("strategy tilt must be within [0, 2]")
    tilt = float(tilt_raw)

    max_periods = backtest_input.get("max_periods", MAX_PERIODS)
    if not isinstance(max_periods, int) or isinstance(max_periods, bool) or not 1 <= max_periods <= MAX_PERIODS:
        raise InputRejected("max_periods is outside the fixed range")

    content, snapshot_sha256 = _read_verified_snapshot(snapshot_ref.get("artifact"), _allowed_roots())
    rows = _parse_rows(content, expected_columns)

    by_period: dict[datetime, dict[str, float]] = {}
    for occurred_at, code, close in rows:
        period_prices = by_period.setdefault(occurred_at, {})
        period_prices[code] = close
    periods = sorted(by_period)
    truncated = len(periods) > max_periods
    periods = periods[:max_periods]
    if len(periods) < 2:
        raise InputRejected("snapshot has too few periods for a simulation")

    market_returns: list[float] = []
    for previous, current in zip(periods, periods[1:]):
        previous_prices = by_period[previous]
        current_prices = by_period[current]
        common = [code for code in previous_prices if code in current_prices and previous_prices[code] > 0.0]
        if not common:
            market_returns.append(0.0)
            continue
        market_returns.append(fmean(current_prices[code] / previous_prices[code] - 1.0 for code in common))

    canonical = json.dumps(parameters, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    seed_text = "|".join((code_sha256, snapshot_sha256, strategy_key, strategy_version, canonical))
    seed = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    backtest_id = seed[:16]
    prng = random.Random(int(seed, 16))
    period_returns = [
        round(market_return * (1.0 + (prng.random() - 0.5) * 2.0 * tilt), 6) for market_return in market_returns
    ]

    uri = snapshot_ref.get("artifact", {}).get("uri")
    source_refs = [str(uri)] if isinstance(uri, str) else []
    reference_time = (gate + timedelta(minutes=1)).isoformat()

    return {
        "contract_id": "backtest-report",
        "contract_version": "1.0.0",
        "backtest_id": backtest_id,
        "strategy_key": strategy_key,
        "strategy_version": strategy_version,
        "code_sha256": code_sha256,
        "snapshot_sha256": snapshot_sha256,
        "reference_time": reference_time,
        "point_in_time_gate": reference_time_raw,
        "parameters": parameters,
        "metrics": _metrics(period_returns, PERIODS_PER_YEAR[data_frequency]),
        "period_returns": period_returns,
        "summary": {
            "simulated": True,
            "source_refs": source_refs,
            "truncated": truncated,
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
