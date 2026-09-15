"""audit.foundation.metric-compute: built-in audit metric calculation.

Consumes a local CSV artifact (ledger-style: entry_id,date,debit_amount,
credit_amount) and produces a metric-output projection with deterministic
audit metrics (row count, unbalanced entries, duplicate pairs, invalid
dates, outlier amounts, balance ratio) plus metric-series points.
Read-only; never mutates the artifact.
"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime, timezone
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    read_verified_artifact,
)

PLUGIN_ID = "audit.foundation.metric-compute"
CAPABILITY = "audit.foundation.metric-compute"
OUTLIER_THRESHOLD = 1_000_000.0
MAX_ROWS = 500_000


def _parse_amount(raw: Any) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    roots = allowed_roots()
    port = envelope.get("metric-input")
    if not isinstance(port, dict) or not isinstance(port.get("artifact"), dict):
        raise InputRejected("metric-input reference is missing")
    artifact = port["artifact"]
    content = read_verified_artifact(artifact, roots)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("artifact is not UTF-8 CSV") from exc
    rows = [dict(row) for row in csv.DictReader(io.StringIO(text))]
    if not rows:
        raise InputRejected("CSV has no data rows")
    if len(rows) > MAX_ROWS:
        raise InputRejected("artifact exceeds row budget")

    seen: dict[str, int] = {}
    unbalanced = 0
    invalid_dates = 0
    outlier_amounts = 0
    total_debit = 0.0
    total_credit = 0.0
    dates: list[date] = []
    for row in rows:
        key = str(row.get("entry_id") or "")
        if key:
            seen[key] = seen.get(key, 0) + 1
        debit = _parse_amount(row.get("debit_amount"))
        credit = _parse_amount(row.get("credit_amount"))
        if debit is not None:
            total_debit += debit
        if credit is not None:
            total_credit += credit
        if debit is not None and credit is not None and abs(debit - credit) > 0.01:
            unbalanced += 1
        if debit is not None and debit >= OUTLIER_THRESHOLD:
            outlier_amounts += 1
        parsed = _parse_date(row.get("date")) if row.get("date") is not None else None
        if row.get("date") is not None and parsed is None:
            invalid_dates += 1
        elif parsed is not None:
            dates.append(parsed)

    duplicate_pairs = sum(1 for count in seen.values() if count > 1)
    balance_ratio = round(total_debit / total_credit, 6) if total_credit else None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    last_date = f"{max(dates).isoformat()}T00:00:00+00:00" if dates else now

    metrics = {
        "row_count": len(rows),
        "unbalanced_entries": unbalanced,
        "duplicate_pairs": duplicate_pairs,
        "invalid_dates": invalid_dates,
        "outlier_amounts": outlier_amounts,
        "total_debit": round(total_debit, 2),
        "total_credit": round(total_credit, 2),
        "balance_ratio": balance_ratio,
    }

    series = [
        {
            "contract_id": "metric-series",
            "contract_version": "1.0.0",
            "series_id": "ledger-row-count",
            "metric": "凭证行数",
            "unit": "rows",
            "window_minutes": 10080,
            "points": [{"at": last_date, "value": len(rows)}],
        },
        {
            "contract_id": "metric-series",
            "contract_version": "1.0.0",
            "series_id": "ledger-amount-total",
            "metric": "借贷总额",
            "unit": "CNY",
            "window_minutes": 10080,
            "points": [{"at": last_date, "value": round(total_debit, 2)}],
        },
        {
            "contract_id": "metric-series",
            "contract_version": "1.0.0",
            "series_id": "ledger-anomaly-rate",
            "metric": "异常行占比",
            "unit": "ratio",
            "window_minutes": 10080,
            "points": [{"at": last_date, "value": round((unbalanced + invalid_dates) / len(rows), 6)}],
        },
    ]

    return {
        "contract_id": "metric-output",
        "contract_version": "1.0.0",
        "source": artifact.get("uri", ""),
        "snapshot_sha256": hashlib.sha256(content).hexdigest(),
        "row_count": len(rows),
        "metrics": metrics,
        "series": series,
    }


if __name__ == "__main__":
    from plugins.builtin._child_common import child_main

    raise SystemExit(child_main(handle))
