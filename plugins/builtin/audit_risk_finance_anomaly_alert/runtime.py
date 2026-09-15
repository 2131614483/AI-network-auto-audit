"""audit.risk.finance-anomaly-alert: financial indicator anomaly scanning.

Reads a clean finance-set (JSON), computes indicator deviations and flags
anomaly candidates (anomaly-candidates contract).  Rules:
  duplicate_row      - rows whose fingerprint still repeats after cleaning
  invalid_date       - rows with an unparseable/blank date
  unbalanced_entry   - entries whose debit/credit sums do not balance
  outlier_amount     - single amounts >= amount threshold (default 1e6)
  round_amount       - amounts >= 1e5 that are exact thousands (suspicious)
Read-only projection; candidates are not confirmations.
"""
from __future__ import annotations

from typing import Any

from plugins.builtin._child_common import (
    InputRejected,
    allowed_roots,
    check_identity,
    child_main,
    read_artifact_json,
    read_verified_artifact,
)

PLUGIN_ID = "audit.risk.finance-anomaly-alert"
CAPABILITY = "audit.risk.finance-anomaly-alert"
MAX_CANDIDATES = 10_000
DEFAULT_THRESHOLD = 1_000_000.0


def _candidate(rule_key: str, severity: str, row_ref: str, source_ref: str, score: float, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": f"{rule_key}:{row_ref}",
        "rule_key": rule_key,
        "severity": severity,
        "row_ref": str(row_ref),
        "source_ref": source_ref,
        "reason_code": rule_key.upper(),
        "score": score,
        "evidence": evidence,
    }


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    check_identity(envelope, PLUGIN_ID, CAPABILITY)
    port = envelope.get("clean-finance-set")
    if not isinstance(port, dict):
        raise InputRejected("clean-finance-set reference is missing")
    artifact = port.get("artifact")
    roots = allowed_roots()
    # side-check the raw bytes (sha/size) even though we parse the JSON below
    read_verified_artifact(artifact, roots)
    clean = read_artifact_json(artifact, roots)
    rows = clean.get("rows")
    if not isinstance(rows, list):
        raise InputRejected("clean-finance-set is missing rows")

    threshold = DEFAULT_THRESHOLD
    raw_threshold = port.get("amount_threshold")
    if raw_threshold is not None:
        threshold = float(raw_threshold)
    if threshold < 0 or threshold > 1e15:
        raise InputRejected("amount_threshold is outside budget")

    candidates: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    entry_sums: dict[str, list[float]] = {}

    for index, row in enumerate(rows, start=1):
        if len(candidates) >= MAX_CANDIDATES:
            break
        if not isinstance(row, dict):
            raise InputRejected("clean-finance-set rows must be objects")
        row_ref = str(row.get("source_row") or index)
        source_ref = f"clean:{clean.get('source_sha256', '?')}:row:{row_ref}"

        fingerprint = "|".join(
            f"{k}={row.get(k)}" for k in ("entry_id", "date", "account_code", "description", "debit_amount", "credit_amount")
        )
        if fingerprint in seen:
            candidates.append(
                _candidate(
                    "duplicate_row", "medium", row_ref, source_ref, 0.8,
                    {"fields": {k: row.get(k) for k in ("entry_id", "date", "account_code", "description")},
                     "first_seen_row": seen[fingerprint]},
                )
            )
        else:
            seen[fingerprint] = row_ref

        flags = row.get("row_flags") or []
        if not isinstance(flags, list):
            flags = []
        if "invalid_date" in flags:
            candidates.append(
                _candidate("invalid_date", "medium", row_ref, source_ref, 0.75, {"date": row.get("date")})
            )

        debit = row.get("debit_amount")
        credit = row.get("credit_amount")
        entry_id = str(row.get("entry_id") or "").strip()
        if entry_id:
            entry_sums.setdefault(entry_id, [0.0, 0.0])
            entry_sums[entry_id][0] += float(debit or 0.0)
            entry_sums[entry_id][1] += float(credit or 0.0)
        for label, amount in (("debit_amount", debit), ("credit_amount", credit)):
            if amount is None:
                continue
            value = float(amount)
            if abs(value) >= threshold:
                candidates.append(
                    _candidate(
                        "outlier_amount", "high", row_ref, source_ref, 0.9,
                        {"column": label, "amount": value, "threshold": threshold},
                    )
                )
            if abs(value) >= 100_000.0 and abs(value) % 1000.0 == 0.0:
                candidates.append(
                    _candidate(
                        "round_amount", "low", row_ref, source_ref, 0.6,
                        {"column": label, "amount": value},
                    )
                )

    for entry_id, (debit_sum, credit_sum) in entry_sums.items():
        if abs(debit_sum - credit_sum) > 1e-6:
            candidates.append(
                _candidate(
                    "unbalanced_entry", "high", entry_id, f"clean:{clean.get('source_sha256', '?')}:entry:{entry_id}",
                    0.85, {"entry_id": entry_id, "debit_sum": debit_sum, "credit_sum": credit_sum},
                )
            )

    return {
        "contract_id": "anomaly-candidates",
        "contract_version": "1.0.0",
        "source_ref": f"clean:{clean.get('source_sha256', '?')}",
        "summary": {
            "scanned_rows": len(rows),
            "candidate_count": len(candidates),
            "rules": ["duplicate_row", "invalid_date", "unbalanced_entry", "outlier_amount", "round_amount"],
        },
        "candidates": candidates,
    }


if __name__ == "__main__":
    raise SystemExit(child_main(handle))
