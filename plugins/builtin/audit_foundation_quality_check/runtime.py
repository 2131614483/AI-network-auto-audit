"""audit.foundation.quality-check: data quality validation.

Consumes any local data artifact (CSV, or JSON array / {"rows": [...]} /
{"data": [...]}) and produces a dataset-validation report: csv_parse /
columns / missing_rate / duplicate_timestamps / point_in_time / freshness
checks with deterministic violations.  Read-only; never mutates the artifact.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date, datetime, timezone
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    read_verified_artifact,
)

PLUGIN_ID = "audit.foundation.quality-check"
CAPABILITY = "audit.foundation.quality-check"
EXPECTED_LEDGER_COLUMNS = ("entry_id", "date", "account_code", "description", "debit_amount", "credit_amount")
MAX_ROWS = 500_000


def _rows_from_bytes(content: bytes) -> tuple[list[dict[str, str]], str]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("artifact is not UTF-8 text") from exc
    # JSON first (array of objects, {"rows": [...]}, {"data": [...]})
    stripped = text.lstrip()
    if stripped.startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise InputRejected("JSON array rows must be objects")
        return [dict(row) for row in payload], "json"
    if stripped.startswith("{"):
        payload = json.loads(text)
        rows = None
        if isinstance(payload, dict):
            for key in ("rows", "data", "records"):
                candidate = payload.get(key)
                if isinstance(candidate, list) and all(isinstance(row, dict) for row in candidate):
                    rows = candidate
                    break
        if rows is None:
            raise InputRejected("JSON payload must contain a rows/data array")
        return [dict(row) for row in rows], "json"
    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(row) for row in reader]
    if not rows:
        raise InputRejected("CSV has no data rows")
    return rows, "csv"


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    port = envelope.get("quality-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("quality-input reference is missing")
    artifact = port["artifact"]
    content = read_verified_artifact(artifact, roots)

    checks: list[dict[str, str]] = []
    violations: list[dict[str, str]] = []

    # csv_parse
    try:
        rows, source_kind = _rows_from_bytes(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        checks.append({"check_id": "csv_parse", "status": "fail", "message": f"无法解析数据文件: {exc}"})
        violations.append({"check_id": "csv_parse", "message": f"无法解析数据文件: {exc}"})
        return {
            "contract_id": "dataset-validation",
            "contract_version": "1.0.0",
            "snapshot_sha256": hashlib.sha256(content).hexdigest(),
            "summary": {"valid": False, "checked_columns": 0, "checked_rows": 0, "missing_values": 0},
            "checks": checks,
            "violations": violations,
        }
    if len(rows) > MAX_ROWS:
        raise InputRejected("artifact exceeds row budget")
    checks.append({"check_id": "csv_parse", "status": "pass", "message": f"{source_kind} 解析成功，共 {len(rows)} 行"})

    # columns
    if rows:
        columns = list(rows[0].keys())
        missing = [col for col in EXPECTED_LEDGER_COLUMNS if col not in columns] if "entry_id" in columns else []
        status = "pass" if not missing else "warn"
        message = f"列数 {len(columns)}" + (f"，缺少 {missing}" if missing else "")
        if status == "warn":
            violations.append({"check_id": "columns", "message": message})
    else:
        columns = []
        status, message = "fail", "数据为空"
        violations.append({"check_id": "columns", "message": message})
    checks.append({"check_id": "columns", "status": status, "message": message})

    # missing_rate
    missing_values = 0
    cell_total = 0
    for row in rows:
        for value in row.values():
            cell_total += 1
            if _is_missing(value):
                missing_values += 1
    rate = missing_values / cell_total if cell_total else 0.0
    if rate > 0.05:
        status, message = "fail", f"缺失率 {rate:.2%} 超过 5%"
        violations.append({"check_id": "missing_rate", "message": message})
    elif rate > 0:
        status, message = "warn", f"缺失 {missing_values} 个单元格（{rate:.2%}）"
        violations.append({"check_id": "missing_rate", "message": message})
    else:
        status, message = "pass", "无缺失值"
    checks.append({"check_id": "missing_rate", "status": status, "message": message})

    # duplicate_timestamps: repeated entry_id (or row identity) pairs
    seen: dict[str, int] = {}
    for row in rows:
        key = row.get("entry_id") or json.dumps(row, ensure_ascii=False, sort_keys=True)
        seen[key] = seen.get(key, 0) + 1
    duplicate_pairs = sum(1 for count in seen.values() if count > 1)
    duplicate_names = sorted(name for name, count in seen.items() if count > 1)[:10]
    if duplicate_pairs:
        status = "fail"
        message = f"发现 {duplicate_pairs} 组重复主键：{duplicate_names}"
        violations.append({"check_id": "duplicate_timestamps", "message": message})
    else:
        status, message = "pass", "无重复主键"
    checks.append({"check_id": "duplicate_timestamps", "status": status, "message": message})

    # point_in_time: invalid dates
    invalid_dates: list[str] = []
    parsed_dates: list[date] = []
    for row in rows:
        raw = row.get("date")
        parsed = _parse_date(raw)
        if raw is not None and parsed is None:
            invalid_dates.append(str(row.get("entry_id", "?")))
        elif parsed is not None:
            parsed_dates.append(parsed)
    if invalid_dates:
        status, message = "fail", f"{len(invalid_dates)} 行日期无效：{invalid_dates[:10]}"
        violations.append({"check_id": "point_in_time", "message": message})
    else:
        status, message = "pass", "日期格式全部有效"
    checks.append({"check_id": "point_in_time", "status": status, "message": message})

    # freshness: only when the payload declares a reference "as_of"
    as_of = None
    try:
        decoded = json.loads(content.decode("utf-8"))
        if isinstance(decoded, dict) and isinstance(decoded.get("as_of"), str):
            as_of = datetime.fromisoformat(decoded["as_of"]).replace(tzinfo=timezone.utc)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        as_of = None
    if as_of is not None and parsed_dates:
        last_date = datetime.combine(max(parsed_dates), datetime.min.time(), tzinfo=timezone.utc)
        hours = (as_of - last_date).total_seconds() / 3600
        if hours > 24 * 14:
            status, message = "fail", f"数据最新日期距参考时间 {hours:.0f} 小时，超过 14 天"
            violations.append({"check_id": "freshness", "message": message})
        else:
            status, message = "pass", f"数据新鲜度 {hours:.0f} 小时"
    else:
        status, message = "pass", "未声明参考时间（as_of），跳过新鲜度判定"
    checks.append({"check_id": "freshness", "status": status, "message": message})

    valid = all(check["status"] == "pass" for check in checks)
    summary: dict[str, Any] = {
        "valid": valid,
        "checked_columns": len(columns),
        "checked_rows": len(rows),
        "missing_values": missing_values,
    }
    if duplicate_pairs:
        summary["duplicate_timestamps"] = duplicate_pairs
    if parsed_dates:
        summary["first_timestamp"] = f"{min(parsed_dates).isoformat()}T00:00:00+00:00"
        summary["last_timestamp"] = f"{max(parsed_dates).isoformat()}T00:00:00+00:00"

    return {
        "contract_id": "dataset-validation",
        "contract_version": "1.0.0",
        "snapshot_sha256": hashlib.sha256(content).hexdigest(),
        "summary": summary,
        "checks": checks,
        "violations": violations,
    }


if __name__ == "__main__":
    from plugins.builtin._child_common import child_main

    raise SystemExit(child_main(handle))
