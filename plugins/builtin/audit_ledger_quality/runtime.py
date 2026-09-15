"""Isolated implementation for the verified read-only ledger-quality plugin."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_LEDGER_ROWS = 200_000
MAX_CANDIDATES = 10_000
BALANCE_TOLERANCE = 1e-6
DEFAULT_AMOUNT_THRESHOLD = 1_000_000.0
SUPPORTED_MAPPING_VERSIONS = frozenset({"1.0.0"})
REQUIRED_HEADERS_V1 = ("entry_id", "date", "account_code", "description", "debit_amount", "credit_amount")
PERIOD_PATTERN = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")


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
        raise InputRejected("ledger artifact URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("ledger artifact URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("ledger artifact file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("ledger artifact is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("ledger artifact is outside declared read roots") from exc
    if resolved.suffix.lower() != ".csv":
        raise InputRejected("ledger artifact must be a CSV file")
    return resolved


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


def _normalize_threshold(value: object) -> float:
    threshold = DEFAULT_AMOUNT_THRESHOLD if value is None else float(value)
    if threshold < 0 or threshold > 1e15:
        raise InputRejected("amount_threshold is outside the fixed budget")
    return threshold


def _rule_pack(mapping_version: str, amount_threshold: float) -> tuple[list[str], str]:
    threshold = int(amount_threshold) if amount_threshold.is_integer() else amount_threshold
    rules = [
        "missing_header",
        "missing_or_invalid_amount",
        "duplicate_row",
        "out_of_period",
        "unbalanced_entry",
        "large_amount",
    ]
    pack = {
        "mapping_version": mapping_version,
        "amount_threshold": threshold,
        "balance_tolerance": BALANCE_TOLERANCE,
        "rules": rules,
    }
    canonical = json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return rules, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _candidate(
    rule_key: str,
    severity: str,
    row_ref: str,
    source_ref: str,
    reason_code: str,
    score: float,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "rule_id": f"{rule_key}:{row_ref}",
        "rule_key": rule_key,
        "severity": severity,
        "row_ref": row_ref,
        "source_ref": source_ref,
        "reason_code": reason_code,
        "score": score,
        "evidence": evidence,
    }


def _is_blank(row: dict[str, str]) -> bool:
    return all(str(value).strip() == "" or value is None for value in row.values())


def handle(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("protocol") != "audit-network-plugin-child-v1":
        raise InputRejected("unsupported child protocol")
    if envelope.get("plugin_id") != "audit.ledger-quality":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "audit.ledger.validate":
        raise InputRejected("unexpected capability")
    ledger = envelope.get("ledger")
    if not isinstance(ledger, dict):
        raise InputRejected("ledger reference is missing")
    artifact = ledger.get("artifact")
    if not isinstance(artifact, dict):
        raise InputRejected("ledger artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("ledger artifact exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("ledger artifact size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("ledger artifact sha256 does not match reference")
    mapping_version = ledger.get("schema_mapping_version")
    if not isinstance(mapping_version, str) or mapping_version not in SUPPORTED_MAPPING_VERSIONS:
        raise InputRejected("schema mapping version is not supported by this plugin")
    period = ledger.get("period")
    if not isinstance(period, str) or PERIOD_PATTERN.fullmatch(period) is None:
        raise InputRejected("period must match YYYY-MM")
    amount_threshold = _normalize_threshold(ledger.get("amount_threshold"))
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("ledger artifact must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = [str(header).strip() for header in (reader.fieldnames or [])]
    missing_headers = [header for header in REQUIRED_HEADERS_V1 if header not in headers]
    rules, rule_pack_sha256 = _rule_pack(mapping_version, amount_threshold)

    candidates: list[dict[str, Any]] = []
    row_fingerprints: dict[str, int] = {}
    entry_rows: dict[str, list[tuple[int, float, float]]] = {}
    summary = {
        "total_rows": 0,
        "candidate_count": 0,
        "duplicate_rows": 0,
        "unbalanced_entries": 0,
        "out_of_period_rows": 0,
        "truncated": False,
    }
    if missing_headers:
        summary["missing_header_columns"] = missing_headers
        evidence = {
            "source_sha256": actual_hash,
            "schema_mapping_version": mapping_version,
            "missing_columns": missing_headers,
            "columns_present": headers,
        }
        candidates.append(
            _candidate(
                "missing_header", "high", "*", f"ledger:{actual_hash}:headers", "MISSING_HEADER", 0.9, evidence
            )
        )

    for index, row in enumerate(reader, start=1):
        if _is_blank(row):
            continue
        if index > MAX_LEDGER_ROWS:
            raise InputRejected("ledger exceeds the fixed row budget")
        summary["total_rows"] += 1
        if len(candidates) >= MAX_CANDIDATES:
            summary["truncated"] = True
            break
        row_ref = str(index)
        source_ref = f"ledger:{actual_hash}:row:{row_ref}"

        fingerprint = hashlib.sha256(
            "|".join(f"{key}={row.get(key, '')}" for key in sorted(row)).encode("utf-8")
        ).hexdigest()
        if fingerprint in row_fingerprints:
            summary["duplicate_rows"] += 1
            candidates.append(
                _candidate(
                    "duplicate_row",
                    "medium",
                    row_ref,
                    source_ref,
                    "DUPLICATE_ROW",
                    0.8,
                    {
                        "source_sha256": actual_hash,
                        "row_fingerprint": fingerprint,
                        "first_seen_row": str(row_fingerprints[fingerprint]),
                        "fields": dict(row),
                    },
                )
            )
        else:
            row_fingerprints[fingerprint] = index

        debit_ok, debit = _parse_amount(row.get("debit_amount"))
        credit_ok, credit = _parse_amount(row.get("credit_amount"))
        if not missing_headers and (not debit_ok or not credit_ok):
            candidates.append(
                _candidate(
                    "missing_or_invalid_amount",
                    "medium",
                    row_ref,
                    source_ref,
                    "MISSING_AMOUNT",
                    0.7,
                    {
                        "source_sha256": actual_hash,
                        "row": dict(row),
                        "debit_amount": row.get("debit_amount"),
                        "credit_amount": row.get("credit_amount"),
                    },
                )
            )

        raw_date = row.get("date")
        date_text = str(raw_date).strip() if raw_date is not None else ""
        valid_date = False
        if date_text:
            try:
                datetime.strptime(date_text, "%Y-%m-%d")
                valid_date = True
            except ValueError:
                pass
        if not date_text:
            summary["out_of_period_rows"] += 1
            candidates.append(
                _candidate(
                    "out_of_period",
                    "medium",
                    row_ref,
                    source_ref,
                    "MISSING_DATE",
                    0.75,
                    {"source_sha256": actual_hash, "date": "", "period": period},
                )
            )
        elif not valid_date:
            summary["out_of_period_rows"] += 1
            candidates.append(
                _candidate(
                    "out_of_period",
                    "medium",
                    row_ref,
                    source_ref,
                    "INVALID_DATE",
                    0.75,
                    {"source_sha256": actual_hash, "date": date_text, "period": period},
                )
            )
        elif date_text[:7] != period:
            summary["out_of_period_rows"] += 1
            candidates.append(
                _candidate(
                    "out_of_period",
                    "medium",
                    row_ref,
                    source_ref,
                    "OUT_OF_PERIOD",
                    0.75,
                    {"source_sha256": actual_hash, "date": date_text, "period": period},
                )
            )

        if not missing_headers:
            for label, valid, amount in (("debit_amount", debit_ok, debit), ("credit_amount", credit_ok, credit)):
                if valid and amount >= amount_threshold:
                    candidates.append(
                        _candidate(
                            "large_amount",
                            "low",
                            row_ref,
                            source_ref,
                            "LARGE_AMOUNT",
                            0.6,
                            {
                                "source_sha256": actual_hash,
                                "column": label,
                                "amount": amount,
                                "threshold": amount_threshold,
                            },
                        )
                    )
            entry_id = row.get("entry_id")
            if entry_id:
                entry_rows.setdefault(str(entry_id).strip(), []).append(
                    (index, debit if debit_ok else 0.0, credit if credit_ok else 0.0)
                )

    for entry_id, rows in entry_rows.items():
        if len(rows) < 2:
            continue
        debit_sum = sum(debit for _index, debit, _credit in rows)
        credit_sum = sum(credit for _index, _debit, credit in rows)
        if abs(debit_sum - credit_sum) > BALANCE_TOLERANCE:
            summary["unbalanced_entries"] += 1
            candidates.append(
                _candidate(
                    "unbalanced_entry",
                    "high",
                    entry_id,
                    f"ledger:{actual_hash}:entry:{entry_id}",
                    "UNBALANCED_ENTRY",
                    0.85,
                    {
                        "source_sha256": actual_hash,
                        "entry_id": entry_id,
                        "debit_sum": debit_sum,
                        "credit_sum": credit_sum,
                        "tolerance": BALANCE_TOLERANCE,
                        "row_span": [rows[0][0], rows[-1][0]],
                    },
                )
            )

    summary["candidate_count"] = len(candidates)
    output = {
        "contract_id": "audit-quality-candidates",
        "contract_version": "1.0.0",
        "ledger_sha256": actual_hash,
        "schema_mapping_version": mapping_version,
        "period": period,
        "rule_pack_sha256": rule_pack_sha256,
        "rules": rules,
        "summary": summary,
        "candidates": candidates,
    }
    return output


def main() -> int:
    try:
        envelope = json.loads(sys.stdin.read())
        if not isinstance(envelope, dict):
            raise InputRejected("child envelope must be an object")
        output = handle(envelope)
        sys.stdout.buffer.write(json.dumps({"ok": True, "output": output}, ensure_ascii=False).encode("utf-8"))
        return 0
    except (InputRejected, json.JSONDecodeError) as exc:
        sys.stdout.buffer.write(json.dumps({"ok": False, "error": {"code": "invalid_input", "message": str(exc)}}, ensure_ascii=False).encode("utf-8"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())