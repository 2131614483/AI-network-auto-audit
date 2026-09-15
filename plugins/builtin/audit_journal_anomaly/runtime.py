"""Isolated implementation for the verified read-only journal-anomaly plugin."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_LEDGER_ROWS = 200_000
MAX_CANDIDATES = 10_000
DEFAULT_ZSCORE = 3.0
ROUND_UNIT = 1000.0
SUPPORTED_MAPPING_VERSIONS = frozenset({"1.0.0"})
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
        raise InputRejected("journal artifact URI is missing")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise InputRejected("journal artifact URI must be local")
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise InputRejected("journal artifact file is unavailable") from exc
    if not resolved.is_file():
        raise InputRejected("journal artifact is not a regular file")
    try:
        next(root for root in roots if resolved.is_relative_to(root))
    except StopIteration as exc:
        raise InputRejected("journal artifact is outside declared read roots") from exc
    if resolved.suffix.lower() != ".csv":
        raise InputRejected("journal artifact must be a CSV file")
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


def _normalize_zscore(value: object) -> float:
    threshold = DEFAULT_ZSCORE if value is None else float(value)
    if threshold < 1 or threshold > 10:
        raise InputRejected("zscore_threshold is outside the fixed budget")
    return threshold


def _rule_pack(mapping_version: str, zscore: float) -> tuple[list[str], str]:
    rules = [
        "invalid_date",
        "outlier_amount",
        "negative_amount",
        "round_amount",
        "duplicate_row",
        "missing_amount",
    ]
    pack = {"mapping_version": mapping_version, "zscore_threshold": zscore, "round_unit": ROUND_UNIT, "rules": rules}
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
    if envelope.get("plugin_id") != "audit.journal-anomaly":
        raise InputRejected("unexpected plugin identity")
    if envelope.get("capability") != "audit.journal.detect":
        raise InputRejected("unexpected capability")
    journal = envelope.get("journal")
    if not isinstance(journal, dict):
        raise InputRejected("journal reference is missing")
    artifact = journal.get("artifact")
    if not isinstance(artifact, dict):
        raise InputRejected("journal artifact reference is missing")
    path = _resolve_file(artifact.get("uri"), _allowed_roots())
    content = path.read_bytes()
    if len(content) > MAX_INPUT_BYTES:
        raise InputRejected("journal artifact exceeds local read budget")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise InputRejected("journal artifact size does not match reference")
    expected_hash = artifact.get("sha256")
    actual_hash = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
        raise InputRejected("journal artifact sha256 does not match reference")
    mapping_version = journal.get("schema_mapping_version")
    if not isinstance(mapping_version, str) or mapping_version not in SUPPORTED_MAPPING_VERSIONS:
        raise InputRejected("schema mapping version is not supported by this plugin")
    period = journal.get("period")
    if not isinstance(period, str) or PERIOD_PATTERN.fullmatch(period) is None:
        raise InputRejected("period must match YYYY-MM")
    zscore = _normalize_zscore(journal.get("zscore_threshold"))
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected("journal artifact must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    rules, rule_pack_sha256 = _rule_pack(mapping_version, zscore)

    rows: list[tuple[int, dict[str, str], float]] = []
    row_fingerprints: dict[str, int] = {}
    summary = {
        "total_rows": 0,
        "candidate_count": 0,
        "invalid_dates": 0,
        "outliers": 0,
        "negative_amounts": 0,
        "round_amounts": 0,
        "duplicate_rows": 0,
        "missing_amounts": 0,
        "zscore_threshold": zscore,
        "truncated": False,
    }
    for index, row in enumerate(reader, start=1):
        if _is_blank(row):
            continue
        if index > MAX_LEDGER_ROWS:
            raise InputRejected("journal exceeds the fixed row budget")
        summary["total_rows"] += 1
        amount_ok, amount = _parse_amount(row.get("amount"))
        rows.append((index, dict(row), amount if amount_ok else 0.0))

    candidates: list[dict[str, Any]] = []
    amounts = [amount for _index, _row, amount in rows if _parse_amount(_row.get("amount"))[0]]
    mean = statistics.fmean(amounts) if amounts else 0.0
    stdev = statistics.stdev(amounts) if len(amounts) > 1 else 0.0

    for index, row, amount in rows:
        if len(candidates) >= MAX_CANDIDATES:
            summary["truncated"] = True
            break
        row_ref = str(index)
        source_ref = f"journal:{actual_hash}:row:{row_ref}"

        fingerprint = hashlib.sha256(
            "|".join(f"{key}={row.get(key, '')}" for key in sorted(row)).encode("utf-8")
        ).hexdigest()
        if fingerprint in row_fingerprints:
            summary["duplicate_rows"] += 1
            candidates.append(
                _candidate(
                    "duplicate_row", "medium", row_ref, source_ref, "DUPLICATE_ROW", 0.8,
                    {"source_sha256": actual_hash, "row_fingerprint": fingerprint, "fields": dict(row)},
                )
            )
        else:
            row_fingerprints[fingerprint] = index

        amount_ok, amount = _parse_amount(row.get("amount"))
        if not amount_ok:
            summary["missing_amounts"] += 1
            candidates.append(
                _candidate(
                    "missing_amount", "medium", row_ref, source_ref, "MISSING_AMOUNT", 0.7,
                    {"source_sha256": actual_hash, "row": dict(row)},
                )
            )
        else:
            if amount < 0:
                summary["negative_amounts"] += 1
                candidates.append(
                    _candidate(
                        "negative_amount", "medium", row_ref, source_ref, "NEGATIVE_AMOUNT", 0.7,
                        {"source_sha256": actual_hash, "amount": amount, "row": dict(row)},
                    )
                )
            if stdev > 0 and abs(amount - mean) > zscore * stdev:
                summary["outliers"] += 1
                candidates.append(
                    _candidate(
                        "outlier_amount", "high", row_ref, source_ref, "OUTLIER_AMOUNT", 0.9,
                        {
                            "source_sha256": actual_hash,
                            "amount": amount,
                            "mean": mean,
                            "stdev": stdev,
                            "zscore": (amount - mean) / stdev,
                            "threshold": zscore,
                        },
                    )
                )
            if amount != 0 and abs(amount % ROUND_UNIT) < 1e-6:
                summary["round_amounts"] += 1
                candidates.append(
                    _candidate(
                        "round_amount", "low", row_ref, source_ref, "ROUND_AMOUNT", 0.5,
                        {"source_sha256": actual_hash, "amount": amount, "unit": ROUND_UNIT},
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
        if not date_text or not valid_date:
            summary["invalid_dates"] += 1
            candidates.append(
                _candidate(
                    "invalid_date", "high", row_ref, source_ref, "INVALID_DATE", 0.85,
                    {"source_sha256": actual_hash, "date": date_text},
                )
            )

    summary["candidate_count"] = len(candidates)
    output = {
        "contract_id": "anomaly-candidates",
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
        sys.stdout.buffer.write(
            json.dumps({"ok": False, "error": {"code": "invalid_input", "message": str(exc)}}, ensure_ascii=False).encode("utf-8")
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
