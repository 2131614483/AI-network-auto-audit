"""Isolated implementation for the verified read-only snapshot-guard plugin."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_SNAPSHOT_ROWS = 1_000_000
DEFAULT_FRESHNESS_HOURS = 24.0
DEFAULT_MISSING_RATE = 0.05
TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


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


def _parse_timestamp(raw: object) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "quant.snapshot-guard":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "quant.dataset.validate":
        raise InputRejected("unexpected capability")
    snapshot = envelope.get("snapshot")
    if not isinstance(snapshot, dict):
        raise InputRejected("snapshot reference is missing")
    artifact = snapshot.get("artifact")
    if not isinstance(artifact, dict):
        raise InputRejected("snapshot artifact reference is missing")
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
    reference_time_raw = snapshot.get("reference_time")
    if not isinstance(reference_time_raw, str):
        raise InputRejected("reference_time is missing")
    reference_time = _parse_timestamp(reference_time_raw)
    if reference_time is None:
        raise InputRejected("reference_time must be an ISO date-time")
    max_freshness = float(snapshot.get("max_freshness_hours", DEFAULT_FRESHNESS_HOURS))
    if not (1 <= max_freshness <= 8760):
        raise InputRejected("max_freshness_hours is outside the fixed budget")
    point_in_time = bool(snapshot.get("point_in_time", True))
    expected_columns = snapshot.get("expected_columns")
    if expected_columns is not None and not isinstance(expected_columns, list):
        raise InputRejected("expected_columns must be a list")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("snapshot must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = [str(header).strip() for header in (reader.fieldnames or [])]
    if not headers:
        raise InputRejected("snapshot CSV has no headers")

    checks: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    def add_check(check_id: str, status: str, message: str) -> None:
        checks.append({"check_id": check_id, "status": status, "message": message})
        if status == "fail":
            violations.append({"check_id": check_id, "message": message})

    numeric_columns: list[str] = []
    for header in headers[1:]:
        if header:
            numeric_columns.append(header)
    if expected_columns:
        missing_columns = [column for column in expected_columns if column not in headers]
        if missing_columns:
            add_check("columns", "fail", f"expected columns missing: {', '.join(missing_columns)}")
        else:
            add_check("columns", "pass", f"all {len(expected_columns)} expected columns present")
    else:
        add_check("columns", "pass", f"{len(numeric_columns)} numeric columns found")

    total_rows = 0
    missing_values = 0
    duplicate_timestamps = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    seen_timestamps: set[str] = set()
    for index, row in enumerate(reader, start=1):
        if index > MAX_SNAPSHOT_ROWS:
            raise InputRejected("snapshot exceeds the fixed row budget")
        if all(str(value).strip() == "" for value in row.values()):
            continue
        total_rows += 1
        raw_time = row.get(headers[0]) if headers else None
        parsed_time = _parse_timestamp(raw_time)
        if parsed_time is None:
            missing_values += 1
        else:
            if first_timestamp is None or parsed_time < first_timestamp:
                first_timestamp = parsed_time
            if last_timestamp is None or parsed_time > last_timestamp:
                last_timestamp = parsed_time
            key = str(raw_time).strip()
            if key in seen_timestamps:
                duplicate_timestamps += 1
            seen_timestamps.add(key)
        for column in numeric_columns:
            value = row.get(column)
            if value is None or str(value).strip() == "":
                missing_values += 1

    if total_rows == 0:
        add_check("csv_parse", "fail", "snapshot has no data rows")
        missing_rate = 1.0
    else:
        missing_rate = missing_values / (total_rows * max(1, len(numeric_columns) + 1))
        add_check("csv_parse", "pass", f"parsed {total_rows} rows across {len(headers)} columns")
        if duplicate_timestamps:
            add_check("duplicate_timestamps", "warn", f"{duplicate_timestamps} duplicate timestamps found")
        else:
            add_check("duplicate_timestamps", "pass", "no duplicate timestamps")

    if last_timestamp is not None:
        freshness_hours = (reference_time - last_timestamp).total_seconds() / 3600.0
        if freshness_hours < 0:
            add_check("point_in_time", "fail", "snapshot contains future timestamps beyond the reference point")
        elif point_in_time and freshness_hours > max_freshness:
            add_check("freshness", "fail", f"snapshot is {freshness_hours:.2f}h stale (max {max_freshness:.0f}h)")
        elif point_in_time:
            add_check("freshness", "pass", f"snapshot is {freshness_hours:.2f}h old")
        else:
            add_check("freshness", "warn", "freshness gate disabled by caller")
    else:
        freshness_hours = -1.0
        add_check("point_in_time", "fail", "no valid timestamps found")

    if missing_rate <= DEFAULT_MISSING_RATE:
        add_check("missing_rate", "pass", f"missing rate {missing_rate:.2%} within budget")
    else:
        add_check("missing_rate", "warn", f"missing rate {missing_rate:.2%} exceeds {DEFAULT_MISSING_RATE:.0%}")

    valid = all(check["status"] != "fail" for check in checks)
    return {
        "contract_id": "dataset-validation",
        "contract_version": "1.0.0",
        "snapshot_sha256": actual_hash,
        "summary": {
            "valid": valid,
            "checked_columns": len(numeric_columns),
            "checked_rows": total_rows,
            "missing_values": missing_values,
            "duplicate_timestamps": duplicate_timestamps,
            "first_timestamp": first_timestamp.isoformat() if first_timestamp is not None else "",
            "last_timestamp": last_timestamp.isoformat() if last_timestamp is not None else "",
            "freshness_hours": freshness_hours if freshness_hours >= 0 else 0,
        },
        "checks": checks,
        "violations": violations,
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
