"""audit.foundation.finance-clean: deterministic ledger cleaning.

Reads a raw ledger artifact (CSV via ledger-artifact-ref envelope), normalizes
rows (dedupe, drop blank, tag invalid dates/amounts), and emits a clean
finance-set with a per-row status and a cleaning summary.  Read-only: the
output is a projection; nothing is written outside the executor's node dir.
"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime
from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_verified_artifact,
)

PLUGIN_ID = "audit.foundation.finance-clean"
CAPABILITY = "audit.foundation.finance-clean"
REQUIRED_HEADERS = ("entry_id", "date", "account_code", "description", "debit_amount", "credit_amount")
MAX_ROWS = 200_000
BALANCE_TOLERANCE = 1e-6


def _parse_amount(raw: object) -> tuple[bool, float]:
    if raw is None:
        return False, 0.0
    text = str(raw).strip().replace(",", "")
    if not text:
        return False, 0.0
    try:
        return True, float(text)
    except ValueError:
        return False, 0.0


def _valid_date(text: str) -> bool:
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("raw-finance-set")
    if not isinstance(port, dict):
        raise InputRejected("raw-finance-set reference is missing")
    artifact = port.get("artifact")
    roots = allowed_roots()
    content = read_verified_artifact(artifact, roots)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("ledger must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = [str(h).strip() for h in (reader.fieldnames or [])]
    missing = [h for h in REQUIRED_HEADERS if h not in headers]

    rows: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    summary = {"input_rows": 0, "kept_rows": 0, "duplicates_removed": 0, "invalid_dates": 0, "missing_amounts": 0, "unbalanced_entries": 0}
    entry_debits: dict[str, float] = {}
    entry_credits: dict[str, float] = {}

    if missing:
        raise InputRejected(f"ledger missing required headers: {missing}")

    for index, row in enumerate(reader, start=1):
        if index > MAX_ROWS:
            raise InputRejected("ledger exceeds row budget")
        if all(str(v).strip() == "" for v in row.values()):
            continue
        summary["input_rows"] += 1

        fingerprint = hashlib.sha256(
            "|".join(f"{k}={row.get(k, '')}" for k in sorted(row)).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen:
            summary["duplicates_removed"] += 1
            continue
        seen[fingerprint] = index

        date_text = str(row.get("date") or "").strip()
        date_ok = _valid_date(date_text) if date_text else False
        if not date_ok:
            summary["invalid_dates"] += 1

        debit_ok, debit = _parse_amount(row.get("debit_amount"))
        credit_ok, credit = _parse_amount(row.get("credit_amount"))
        if not debit_ok or not credit_ok:
            summary["missing_amounts"] += 1

        entry_id = str(row.get("entry_id") or "").strip()
        if entry_id:
            entry_debits[entry_id] = entry_debits.get(entry_id, 0.0) + (debit if debit_ok else 0.0)
            entry_credits[entry_id] = entry_credits.get(entry_id, 0.0) + (credit if credit_ok else 0.0)

        rows.append(
            {
                "entry_id": entry_id,
                "date": date_text,
                "account_code": str(row.get("account_code") or "").strip(),
                "description": str(row.get("description") or "").strip(),
                "debit_amount": debit if debit_ok else None,
                "credit_amount": credit if credit_ok else None,
                "row_flags": [
                    *(["invalid_date"] if not date_ok else []),
                    *(["missing_amount"] if (not debit_ok or not credit_ok) else []),
                ],
                "source_row": index,
            }
        )

    for entry_id, debit_sum in entry_debits.items():
        credit_sum = entry_credits.get(entry_id, 0.0)
        if abs(debit_sum - credit_sum) > BALANCE_TOLERANCE:
            summary["unbalanced_entries"] += 1

    summary["kept_rows"] = len(rows)
    return {
        "contract_id": "clean-finance-set",
        "contract_version": "1.0.0",
        "source_sha256": str(artifact.get("sha256", "")).lower(),
        "summary": summary,
        "rows": rows,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
