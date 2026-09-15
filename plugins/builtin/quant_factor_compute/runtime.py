"""Isolated implementation for the verified read-only factor-compute plugin."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_SNAPSHOT_ROWS = 1_000_000
TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")
SUPPORTED_FACTORS = ("returns", "volatility", "momentum", "zscore", "drawdown", "volume_zscore")
PRICE_COLUMN_HINTS = ("close", "price", "adjusted_close", "adj_close")


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
    if resolved.suffix.lower() != ".csv":
        raise InputRejected("snapshot must be a CSV file")
    return resolved


def _parse_float(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _factor_pack(factors: tuple[str, ...], lookback: int) -> str:
    canonical = json.dumps({"factors": list(factors), "lookback": lookback}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "stdev": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": statistics.fmean(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
    }


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "quant.factor-compute":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "quant.factor.compute":
        raise InputRejected("unexpected capability")
    dataset = envelope.get("dataset")
    if not isinstance(dataset, dict):
        raise InputRejected("factor input is missing")
    artifact = dataset.get("artifact")
    if not isinstance(artifact, dict):
        raise InputRejected("factor artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
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
    reference_time_raw = dataset.get("reference_time")
    if not isinstance(reference_time_raw, str):
        raise InputRejected("reference_time is missing")
    requested = dataset.get("factors")
    if not isinstance(requested, list) or not requested:
        raise InputRejected("factors are missing")
    if len(requested) > 8 or len(set(requested)) != len(requested):
        raise InputRejected("factors exceed the fixed budget")
    for factor in requested:
        if factor not in SUPPORTED_FACTORS:
            raise InputRejected("unsupported factor requested")
    lookback = dataset.get("lookback")
    if not isinstance(lookback, int) or not (2 <= lookback <= 10000):
        raise InputRejected("lookback is outside the fixed budget")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("snapshot must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = [str(header).strip() for header in (reader.fieldnames or [])]
    if not headers:
        raise InputRejected("snapshot CSV has no headers")
    numeric_headers = [header for header in headers[1:] if header]
    price_column = next((hint for hint in PRICE_COLUMN_HINTS if hint in headers), numeric_headers[0] if numeric_headers else "")
    volume_column = "volume" if "volume" in headers else ("成交量" if "成交量" in headers else "")

    prices: list[float] = []
    volumes: list[float] = []
    for index, row in enumerate(reader, start=1):
        if index > MAX_SNAPSHOT_ROWS:
            raise InputRejected("snapshot exceeds the fixed row budget")
        if all(str(value).strip() == "" for value in row.values()):
            continue
        if price_column:
            price = _parse_float(row.get(price_column))
            if price is not None:
                prices.append(price)
        if volume_column:
            volume = _parse_float(row.get(volume_column))
            if volume is not None:
                volumes.append(volume)

    if len(prices) < 2:
        raise InputRejected("snapshot has too few numeric points for factor computation")

    window = prices[-lookback:]
    factors: list[dict[str, Any]] = []
    series_ref = f"factor:{actual_hash}:price_column:{price_column}"

    def add_factor(factor_id: str, name: str, value: float, n: int, stats_values: list[float]) -> None:
        factors.append(
            {
                "factor_id": factor_id,
                "name": name,
                "value": round(value, 8),
                "n": n,
                "series_ref": series_ref,
                "source_refs": [f"snapshot:{actual_hash}"],
                **{key: round(item, 8) for key, item in _stats(stats_values).items()},
            }
        )

    for factor in requested:
        if factor == "returns":
            last_return = (prices[-1] / prices[-2]) - 1 if prices[-2] else 0.0
            add_factor("returns", "最新单期收益", last_return, len(window), window)
        elif factor == "volatility":
            window_returns = [(window[i] / window[i - 1]) - 1 for i in range(1, len(window)) if window[i - 1]]
            add_factor("volatility", "回看窗口收益波动率", statistics.stdev(window_returns) if len(window_returns) > 1 else 0.0, len(window_returns), window_returns)
        elif factor == "momentum":
            base = prices[-lookback - 1] if len(prices) > lookback else prices[0]
            momentum = (prices[-1] / base) - 1 if base else 0.0
            add_factor("momentum", f"{lookback} 期动量", momentum, len(window), window)
        elif factor == "zscore":
            latest = prices[-1]
            mean = statistics.fmean(window)
            stdev = statistics.stdev(window) if len(window) > 1 else 0.0
            zscore = (latest - mean) / stdev if stdev else 0.0
            add_factor("zscore", "最新值 Z 分数", zscore, len(window), window)
        elif factor == "drawdown":
            peak = 0.0
            max_drawdown = 0.0
            for price in window:
                peak = max(peak, price)
                if peak:
                    max_drawdown = min(max_drawdown, (price / peak) - 1)
            add_factor("drawdown", "回看窗口最大回撤", max_drawdown, len(window), window)
        elif factor == "volume_zscore":
            if not volumes:
                raise InputRejected("volume_zscore requested but no volume column exists")
            vol_window = volumes[-lookback:]
            mean = statistics.fmean(vol_window)
            stdev = statistics.stdev(vol_window) if len(vol_window) > 1 else 0.0
            value = (volumes[-1] - mean) / stdev if stdev else 0.0
            add_factor("volume_zscore", "成交量 Z 分数", value, len(vol_window), vol_window)

    return {
        "contract_id": "factor-artifact-ref",
        "contract_version": "1.0.0",
        "snapshot_sha256": actual_hash,
        "reference_time": reference_time_raw,
        "factor_pack_sha256": _factor_pack(tuple(requested), lookback),
        "summary": {
            "factors_computed": len(factors),
            "series_points": len(prices),
            "truncated": False,
        },
        "factors": factors,
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
